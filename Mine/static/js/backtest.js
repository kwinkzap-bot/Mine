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
    let lotSizeBySymbol = {};   // populated from /api/backtest/symbols' lot_sizes

    // Tracks the in-flight 2nd-Candle optimise so a new/changed run cancels
    // the old one. Declared up top so cancelScOptimise() is safe to call
    // during init.
    let _scOptRun    = 0;     // generation token — only the latest run is honoured
    let _scOptAbort  = null;  // AbortController for the in-flight POST request
    // Same generation-token pattern for the 30-Min Fakeout optimise.
    let _tmfOptRun   = 0;
    let _wcbOptRun   = 0;
    let _tmfOptAbort = null;
    // Same generation-token pattern for the Scalp Pullback optimise.
    let _spOptRun    = 0;
    let _spOptAbort  = null;
    // Same generation-token pattern for the Pivot Confluence optimise.
    let _pcOptRun    = 0;
    let _pcOptAbort  = null;
    // The 30-Sec Option Breakout run is backgrounded on the server too, so it
    // needs the same generation token — switching strategy mid-fetch must stop
    // the poll rather than render a stale result under the new strategy's
    // column set.
    let _obRun       = 0;
    let _obAbort     = null;
    let _obOptRun    = 0;
    let _obOptAbort  = null;
    // True while a "Find Best Params" sweep owns the shared #loading card
    // (see _showOptLoader far below). Declared up here because the cancel*
    // helpers call _hideOptLoader() during init, before that block runs —
    // a `let` down there would still be in its temporal dead zone.
    let _optLoaderActive = false;

    // ── Live-algo configs (LIVE badges) ──────────────────────────────────
    // Param sets currently running as live algos. Used to flag the backtest
    // form selection and the Best Params grid rows that are already live.
    let _liveConfigs = { sc: null };
    const LIVE_BADGE_HTML = '<span class="opt-live-badge">LIVE</span>';

    function _isScComboLive(cfg) {
        const v = _liveConfigs.sc;
        return !!v && v.interval === cfg.interval &&
            Number(v.candle_index) === Number(cfg.candle_index) &&
            Number(v.rr_ratio)     === Number(cfg.rr_ratio) &&
            v.direction === cfg.direction;
    }

    // Backtest-form selection → LIVE flag next to the strategy picker.
    function updateLiveFlagBadge() {
        const badge = document.getElementById('liveFlagBadge');
        if (!badge) return;
        const strat = document.getElementById('strategySelect')?.value || '';
        let live = false;
        if (strat === 'second_candle') {
            live = _isScComboLive({
                interval:     document.getElementById('interval')?.value,
                candle_index: parseInt(document.getElementById('scCandleIndex')?.value || 2),
                rr_ratio:     parseFloat(document.getElementById('scRrRatio')?.value || 3),
                direction:    document.getElementById('scDirection')?.value || 'both',
            });
        }
        badge.style.display = live ? '' : 'none';
    }

    async function refreshLiveConfigs() {
        try {
            const resp = await fetch('/api/algo/live-configs');
            const data = await resp.json();
            if (data && data.success) {
                _liveConfigs = { sc: data.second_candle || null };
                // Re-badge anything already rendered — same rows/sort, but the
                // Live column's isLive() check re-reads the fresh _liveConfigs.
                Object.values(_scOptGroupsByTf).forEach(st => DataGrid.refresh(st.gridEl));
                updateLiveFlagBadge();
            }
        } catch (e) {
            console.warn('live-configs fetch failed:', e);
        }
    }
    refreshLiveConfigs();
    ['strategySelect', 'interval',
     'scCandleIndex', 'scRrRatio', 'scDirection'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', updateLiveFlagBadge);
    });

    // Initialize dates (default to Jan 1st 2017)
    const today = new Date();

    // Today in the browser's own timezone. toISOString() is UTC, which in IST
    // reads back as yesterday between 00:00 and 05:30.
    function localToday() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    // N days back, in the browser's own timezone — same reason as localToday().
    function daysAgoLocal(n) {
        const d = new Date();
        d.setDate(d.getDate() - n);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

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
                if (data.lot_sizes) lotSizeBySymbol = data.lot_sizes;
                console.log('Loaded symbols:', allSymbols.length);
                // The page loads with NIFTY pre-selected before this fetch
                // resolves — re-apply now that real lot sizes are in.
                applyLotValueForSymbol(selectedSymbol);
                _fetchTmfSymbolDefaults();
                _fetchEmaSymbolDefaults();
            } else {
                if (window.showNotification) window.showNotification('Error loading symbols: ' + data.error, 'error');
            }
        } catch (error) {
            console.error('Failed to fetch symbols:', error);
        }
    }

    // Fills the TMF "Symbol" dropdown (defaults to "All Stocks", i.e. the
    // full scan) from the fixed TMF stock list — not every F&O stock.
    function _populateTmfSymbolDropdown(symbols) {
        const sel = document.getElementById('tmfSymbol');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">All Stocks (full scan)</option>' +
            symbols.slice().sort().map(s => `<option value="${s}">${s}</option>`).join('');
        sel.value = current || '';
    }

    // The same per-stock best-combo table the backend applies server-side
    // (_TMF_SYMBOL_DEFAULTS in api.py) — fetched once so picking a symbol
    // can preview its combo in the form immediately, without a round trip.
    // Its keys are also the TMF strategy's entire stock universe (a fixed,
    // user-supplied list, not every F&O stock), so the same fetch fills
    // the Symbol dropdown.
    let _tmfSymbolDefaults = {};
    async function _fetchTmfSymbolDefaults() {
        try {
            const res = await fetch('/api/backtest/thirty-min-fakeout/symbol-defaults');
            const data = await res.json();
            if (data.success) {
                _tmfSymbolDefaults = data.defaults || {};
                _populateTmfSymbolDropdown(Object.keys(_tmfSymbolDefaults));
            }
        } catch (error) {
            console.error('Failed to fetch TMF symbol defaults:', error);
        }
    }

    // Per-symbol default Direction/Target for EMA Confluence Breakout
    // (EMA_SYMBOL_DEFAULTS in ema_symbol_universe.py) — fetched once so
    // picking a symbol in the shared Symbol box can preview its own
    // defaults immediately, same idea as the TMF table above.
    let _emaSymbolDefaults = {};
    async function _fetchEmaSymbolDefaults() {
        try {
            const res = await fetch('/api/backtest/ema-pullback/symbol-defaults');
            const data = await res.json();
            if (data.success) {
                _emaSymbolDefaults = data.defaults || {};
                applyEmaDefaultsForSymbol(selectedSymbol);
            }
        } catch (error) {
            console.error('Failed to fetch EMA symbol defaults:', error);
        }
    }

    // Applies symbol's own Direction/Target defaults to the EMA Confluence
    // Breakout fields — a display update only (like applyLotValueForSymbol).
    //
    // A symbol OUTSIDE the table gets no invented Both/5% any more. That table
    // is the strategy's whole universe: the live algo scans exactly those
    // stocks, so pre-filling plausible-looking settings for anything else let
    // the form run a real-looking backtest for a stock the strategy will never
    // trade. The fields are left untouched and the notice below says why —
    // and the server refuses the run regardless (see run_ema_pullback_backtest_api).
    window.applyEmaDefaultsForSymbol = function(symbol) {
        const note = document.getElementById('emaSymbolNote');
        const sym  = (symbol || '').toUpperCase();
        // "All Stocks" has no single Direction/Target to preview — every
        // stock uses its own row of the table server-side.
        // Literal, not the ALL_STOCKS const — that is declared further down the
        // file, so referencing it here would sit in its temporal dead zone.
        if (sym === 'ALL STOCKS') { if (note) note.style.display = 'none'; return; }

        const d = _emaSymbolDefaults[sym];
        if (!d) {
            if (note && sym) {
                note.textContent = `${sym} is not an EMA Confluence symbol — this strategy runs `
                    + `only on the ${Object.keys(_emaSymbolDefaults).length} stocks in its own `
                    + `universe (the same ones the live algo scans).`;
                note.style.display = '';
            } else if (note) {
                note.style.display = 'none';
            }
            return;
        }
        if (note) note.style.display = 'none';
        const dirSel   = document.getElementById('emaDirection');
        const tgtInput = document.getElementById('emaTargetPct');
        if (dirSel)   dirSel.value   = d.direction;
        if (tgtInput) tgtInput.value = d.target_pct;
    };

    // Picking a specific stock previews ITS OWN best combo in the Direction/
    // filter fields — purely a display update; the backend looks the combo
    // up by symbol itself whenever "Use Per-Stock Best Settings" is on, so
    // this just keeps what's shown in sync with what will actually run.
    // Switching back to "All Stocks" leaves the fields alone (each stock in
    // that scan gets its own combo server-side regardless of what's shown).
    const tmfSymbolSelect = document.getElementById('tmfSymbol');
    if (tmfSymbolSelect) {
        tmfSymbolSelect.addEventListener('change', () => {
            const sym = tmfSymbolSelect.value;
            const d = sym && _tmfSymbolDefaults[sym];
            if (!d) return;
            const dir = document.getElementById('tmfDirection');
            const entryBuf = document.getElementById('tmfUseEntryBuffer');
            const bodyFilt = document.getElementById('tmfUseBodyFilter');
            const c2Filt   = document.getElementById('tmfUseC2CloseFilter');
            if (dir) dir.value = d.direction;
            if (entryBuf) entryBuf.checked = !!d.use_entry_buffer;
            if (bodyFilt) bodyFilt.checked = !!d.use_body_filter;
            if (c2Filt) c2Filt.checked = !!d.use_c2_close_filter;
        });
    }

    // ── "All Stocks" (EMA Confluence Breakout only) ──────────────────────
    // A pseudo-symbol offered at the top of the shared Symbol box when EMA
    // Confluence Breakout is selected. Picking it runs the strategy over
    // EVERY stock in EMA_SYMBOL_DEFAULTS instead of one, each with its own
    // Direction/Target and sized off its own lot size server-side — so the
    // per-symbol form fields go read-only while it's active.
    const ALL_STOCKS = 'ALL STOCKS';
    function isEmaStrategy() {
        return (document.getElementById('strategySelect')?.value || '') === 'ema_pullback';
    }
    function isEmaAllMode() {
        return selectedSymbol === ALL_STOCKS && isEmaStrategy();
    }
    // Weekly CPR Breakout offers the same pseudo-symbol: every F&O future
    // stock, or the one picked in the Symbol box.
    function isWcbStrategy() {
        return (document.getElementById('strategySelect')?.value || '') === 'weekly_cpr_breakout';
    }
    // Heikin-Ashi + SuperTrend runs over the same F&O stock universe.
    function isHastStrategy() {
        return (document.getElementById('strategySelect')?.value || '') === 'ha_supertrend';
    }

    // Kicked off only after ALL_STOCKS above exists — its completion callback
    // runs applyLotValueForSymbol(), which reads it.
    fetchSymbols();

    // 2. Searchable Dropdown Logic
    function renderDropdown(filterText = '') {
        const filter = (filterText || '').toUpperCase();
        // Show every matching symbol (not just the first 15) — the list
        // itself scrolls (#symbolList has max-height + overflow-y in CSS),
        // same as the Open Interest page's symbol dropdown.
        const pool = (isEmaStrategy() || isWcbStrategy() || isHastStrategy()) ? [ALL_STOCKS, ...allSymbols] : allSymbols;
        const matches = pool.filter(s => {
            if (!filter) return true;
            return s.includes(filter);
        });

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
                applyEmaDefaultsForSymbol(selectedSymbol);
                syncEmaAllStocksMode();
                cancelScOptimise(); // stale: results would be for the old symbol
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
            applyEmaDefaultsForSymbol(selectedSymbol);
            syncEmaAllStocksMode();
            cancelScOptimise(); // stale: results would be for the old symbol
        }
    });

    // Lot value (₹/pt) per symbol — the exchange's actual lot size for that
    // symbol's nearest F&O contract, fetched from /api/backtest/symbols
    // (see fetchSymbols above). Falls back to NIFTY's lot size (65) for a
    // symbol not yet loaded / not in the F&O universe.
    function lotValueForSymbol(symbol) {
        return lotSizeBySymbol[(symbol || '').toUpperCase()] || 65;
    }
    function applyLotValueForSymbol(symbol) {
        // "All Stocks" has no lot size of its own — the scan sizes each stock
        // by its own contract server-side, so leave the field showing whatever
        // the last real symbol had (it's greyed out in that mode anyway).
        if ((symbol || '').toUpperCase() === ALL_STOCKS) return;
        const lotValue = lotValueForSymbol(symbol);
        ['scLotValue', 'obLotValue', 'emaLotValue'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) el.value = lotValue;
        });
    }

    // Reveals the "Use Per-Stock Direction / Target" toggle while "All Stocks"
    // is selected, and greys out whichever fields that mode takes over:
    // Direction/Target come from each stock's own EMA_SYMBOL_DEFAULTS row
    // (unless the toggle is off), and the ₹/pt always from its own lot size.
    // Find Best Params is a single-symbol sweep, so it hides too.
    function syncEmaAllStocksMode() {
        const all      = isEmaAllMode();
        const perStock = document.getElementById('emaUseSymbolDefaults')?.checked ?? true;
        const note     = document.getElementById('emaAllStocksNote');
        const fields = [
            ['emaDirection', all && perStock, 'Each stock uses its own Direction from the per-stock defaults table'],
            ['emaTargetPct', all && perStock, 'Each stock uses its own Target % from the per-stock defaults table'],
            ['emaLotValue',  all,             'Each stock is sized by its own futures lot size'],
        ];
        fields.forEach(([id, off, why]) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.disabled = off;
            el.title    = off ? why : '';
        });
        if (note) note.style.display = all ? '' : 'none';
        const optBtn = document.getElementById('runOptimiseBtn');
        if (optBtn && isEmaStrategy()) optBtn.style.display = all ? 'none' : '';
        updateEmaInvestment();
    }
    document.getElementById('emaUseSymbolDefaults')?.addEventListener('change', syncEmaAllStocksMode);

    // Seed lot values for the symbol selected on initial page load.
    applyLotValueForSymbol(selectedSymbol);
    applyEmaDefaultsForSymbol(selectedSymbol);

    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        if (!symbolSearch.contains(e.target) && !symbolList.contains(e.target)) {
            symbolList.classList.remove('show');
        }
    });

    // 2.5 Strategy Selection Logic
    const strategySelect = document.getElementById('strategySelect');

    // Simple Trader group — the Quantman run "BB PP ST RSI WR with SL NIFTY CE" on the ST + BB + RSI
    // engine: 3-min candles; BB(15, 3), Williams %R(14) > -30, RSI(14) > 70, SuperTrend(10, 2),
    // close above R1; buy the ≈250 current-week CE; exit on a close below SuperTrend; stop-loss 13 %
    // with TSL X 15 % / Y 10 %; 09:15-15:14, one transaction a day. Re-applied each time the strategy is picked.
    // Simple Trader strategies: 'simpler_trader' is #1; the rest each have their own engine.
    const SIMPLE_STRATS = ['simpler_trader', 'st_ema_rsi_cci', 'st_rsi_pp_bb', 'st_rsi_pp_bb_fut',
                           'st_rsi_cci_wr_pp_bb_fut', 'st_rsi_cpr_bb_sx', 'st_rsi_cpr_bb_wr_sx'];

    // BANKNIFTY runs different numbers. Swept offline on 2019-04 → 2026-10 candles, priced on the 500 option
    // from the cached Breeze minutes (the page's own pricing): max drawdown ₹8.8k at 1 lot (65/pt) against
    // ₹20.4k for the previous BANKNIFTY row and ₹34.4k for the NIFTY numbers, net ₹2.87L, PF 5.6, 109 trades.
    // The 8 % stop does the most (6-13 % all sit under ₹11k here); 2024 was flat, so treat it as capital protection.
    const SIMPLER_BN = { rsi: 65, stLen: 14, stFactor: 1.5, bbLen: 20, bbMult: 3, wrLevel: -10, start: '10:30',
                         slPct: 8, tslX: 0, tslY: 0, premium: '500' };

    function applySimplerTraderPreset() {
        const isBn = ((symbolSearch && symbolSearch.value) || '').toUpperCase().includes('BANKNIFTY');
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        const on  = (id, v) => { const el = document.getElementById(id); if (el) el.checked = v; };
        set('sbrDirection', 'long');
        set('sbrHtf', 'off');
        ['sbrUseRsi', 'sbrUseBb', 'sbrUseSt', 'sbrUsePivot', 'sbrUseWr'].forEach(id => on(id, true));
        on('sbrGreen', false); on('sbrCompound', false);
        set('sbrRsi', 70); set('sbrRsiLen', 14);
        set('sbrStLen', 10); set('sbrStFactor', 2);
        set('sbrBbLen', 15); set('sbrBbMult', 3); set('sbrBbOffset', 0);
        set('sbrWrLen', 14); set('sbrWrLevel', -30);
        set('sbrSlPct', 13); set('sbrTslX', 15); set('sbrTslY', 10);
        set('sbrEntryStart', '09:15'); set('sbrEntryEnd', '15:14'); set('sbrExitCutoff', '15:14');
        set('sbrMaxTrades', 1);
        const prem = document.getElementById('sbrPremium');
        if (prem && [...prem.options].some(o => o.value === '250')) prem.value = '250';
        if (isBn) {
            set('sbrRsi', SIMPLER_BN.rsi);
            set('sbrStLen', SIMPLER_BN.stLen); set('sbrStFactor', SIMPLER_BN.stFactor);
            set('sbrBbLen', SIMPLER_BN.bbLen); set('sbrBbMult', SIMPLER_BN.bbMult);
            set('sbrWrLevel', SIMPLER_BN.wrLevel); set('sbrEntryStart', SIMPLER_BN.start);
            set('sbrSlPct', SIMPLER_BN.slPct); set('sbrTslX', SIMPLER_BN.tslX); set('sbrTslY', SIMPLER_BN.tslY);
            if (prem && [...prem.options].some(o => o.value === SIMPLER_BN.premium)) prem.value = SIMPLER_BN.premium;
        }
    }

    function updateStrategyView() {
        if (!strategySelect) return;

        // Switching strategy abandons any in-flight 2nd-Candle / Pivot
        // Confluence optimise run.
        if (typeof cancelScOptimise === 'function') cancelScOptimise();
        if (typeof cancelPcOptimise === 'function') cancelPcOptimise();
        if (typeof cancelObRun === 'function') cancelObRun();
        if (typeof cancelObOptimise === 'function') cancelObOptimise();

        const intervalSelect = document.getElementById('interval');
        const startDateInput = document.getElementById('startDate');
        const mainInputsRow  = document.getElementById('mainInputsRow');
        const today = new Date();
        const val   = strategySelect.value;

        // Reset all rows
        const smParamsRow   = document.getElementById('swingMomentumParamsRow');
        const scParamsRow   = document.getElementById('secondCandleParamsRow');
        const scLotRow      = document.getElementById('secondCandleLotRow');
        const scOptPanel    = document.getElementById('secondCandleOptimisePanel');
        const obParamsRow   = document.getElementById('optionBreakoutParamsRow');
        const obLotRow      = document.getElementById('optionBreakoutLotRow');
        const obOptPanel    = document.getElementById('optionBreakoutOptimisePanel');
        const expParamsRow  = document.getElementById('expiryBreakoutParamsRow');
        const tmfParamsRow  = document.getElementById('thirtyMinFakeoutParamsRow');
        const tmfOptPanel   = document.getElementById('thirtyMinFakeoutOptimisePanel');
        const emaParamsRow  = document.getElementById('emaPullbackParamsRow');
        const emaLotRow     = document.getElementById('emaPullbackLotRow');
        const emaOptPanel   = document.getElementById('emaOptimisePanel');
        const spParamsRow   = document.getElementById('scalpPullbackParamsRow');
        const spLotRow      = document.getElementById('scalpPullbackLotRow');
        const spOptPanel    = document.getElementById('scalpPullbackOptimisePanel');
        const pcParamsRow   = document.getElementById('pivotConfluenceParamsRow');
        const pcLotRow      = document.getElementById('pivotConfluenceLotRow');
        const pcOptPanel    = document.getElementById('pivotConfluenceOptimisePanel');
        const wcbParamsRow  = document.getElementById('weeklyCprBreakoutParamsRow');
        const sbrParamsRow  = document.getElementById('stBbRsiParamsRow');
        if (sbrParamsRow)  sbrParamsRow.style.display  = 'none';
        { const _sr = document.getElementById('simplerStatsRow'); if (_sr) _sr.style.display = 'none'; }
        const hmParamsRow   = document.getElementById('hilegaMilegaParamsRow');
        if (hmParamsRow)   hmParamsRow.style.display   = 'none';
        const mhParamsRow   = document.getElementById('maHtfParamsRow');
        if (mhParamsRow)   mhParamsRow.style.display   = 'none';
        const secParamsRow  = document.getElementById('stEmaParamsRow');
        if (secParamsRow)  secParamsRow.style.display  = 'none';
        const srpParamsRow  = document.getElementById('stRppParamsRow');
        if (srpParamsRow)  srpParamsRow.style.display  = 'none';
        const srfParamsRow  = document.getElementById('stRpfParamsRow');
        if (srfParamsRow)  srfParamsRow.style.display  = 'none';
        const scwParamsRow  = document.getElementById('stRcwParamsRow');
        if (scwParamsRow)  scwParamsRow.style.display  = 'none';
        const scxParamsRow  = document.getElementById('stScxParamsRow');
        if (scxParamsRow)  scxParamsRow.style.display  = 'none';
        const swxParamsRow  = document.getElementById('stSwxParamsRow');
        if (swxParamsRow)  swxParamsRow.style.display  = 'none';
        // 1-Hour / 1-Day exist for Hilega Milega only (which offers nothing shorter); other strategies do not offer them.
        document.querySelectorAll('#interval option[data-hm]').forEach(o => o.remove());
        document.querySelectorAll('#interval option').forEach(o => { o.hidden = false; o.disabled = false; });
        { const _iv = document.getElementById('interval'); if (_iv) _iv.disabled = false; }   // HA + SuperTrend locks it
        if (wcbParamsRow)  wcbParamsRow.style.display  = 'none';
        const hastParamsRow = document.getElementById('haSupertrendParamsRow');
        if (hastParamsRow) hastParamsRow.style.display = 'none';
        const osParamsRow   = document.getElementById('optionSellingParamsRow');
        if (osParamsRow)   osParamsRow.style.display   = 'none';
        const osOptPanel    = document.getElementById('optionSellingOptimisePanel');
        const sbrOptPanel   = document.getElementById('stBbRsiOptimisePanel');
        if (sbrOptPanel)   sbrOptPanel.style.display   = 'none';
        if (osOptPanel)    osOptPanel.style.display    = 'none';
        const osResultsEl   = document.getElementById('osResults');
        if (osResultsEl)   osResultsEl.style.display   = 'none';
        if (smParamsRow)   smParamsRow.style.display   = 'none';
        if (scParamsRow)   scParamsRow.style.display   = 'none';
        if (scLotRow)      scLotRow.style.display      = 'none';
        if (scOptPanel)    scOptPanel.style.display    = 'none';
        if (obParamsRow)   obParamsRow.style.display   = 'none';
        if (obLotRow)      obLotRow.style.display      = 'none';
        if (obOptPanel)    obOptPanel.style.display    = 'none';
        if (expParamsRow)  expParamsRow.style.display  = 'none';
        if (tmfParamsRow)  tmfParamsRow.style.display  = 'none';
        if (tmfOptPanel)   tmfOptPanel.style.display   = 'none';
        if (emaParamsRow)  emaParamsRow.style.display  = 'none';
        if (emaLotRow)     emaLotRow.style.display     = 'none';
        if (emaOptPanel)   emaOptPanel.style.display   = 'none';
        if (spParamsRow)   spParamsRow.style.display   = 'none';
        if (spLotRow)      spLotRow.style.display      = 'none';
        if (spOptPanel)    spOptPanel.style.display    = 'none';
        if (pcParamsRow)   pcParamsRow.style.display   = 'none';
        if (pcLotRow)      pcLotRow.style.display      = 'none';
        if (pcOptPanel)    pcOptPanel.style.display    = 'none';
        // Restore symbol/date/interval visibility (hidden for some strategies)
        const symFg      = document.getElementById('mainSymbolFg');
        const intFg      = document.getElementById('mainIntervalFg');
        const startDateFg = document.getElementById('startDateFg');
        const endDateFg   = document.getElementById('endDateFg');
        if (symFg) symFg.style.display = '';
        if (intFg) intFg.style.display = '';
        if (startDateFg) startDateFg.style.display = '';
        if (endDateFg)   endDateFg.style.display   = '';

        const optBtn         = document.getElementById('runOptimiseBtn');
        const smGoLiveBtn    = document.getElementById('smGoLiveBtn');
        if (optBtn)       optBtn.style.display       = (val === 'swing_momentum' || val === 'second_candle' || val === 'option_breakout' || val === 'thirty_min_fakeout' || val === 'ema_pullback' || val === 'scalp_pullback' || val === 'pivot_confluence' || val === 'weekly_cpr_breakout' || val === 'option_selling' || SIMPLE_STRATS.includes(val)) ? '' : 'none';
        if (smGoLiveBtn)  smGoLiveBtn.style.display  = (val === 'swing_momentum') ? '' : 'none';

        // Hide optimise result panels when switching strategies
        const smOptPanel  = document.getElementById('smOptimisePanel');
        if (smOptPanel)  smOptPanel.style.display  = 'none';
        const wcbOptPanel = document.getElementById('weeklyCprBreakoutOptimisePanel');
        if (wcbOptPanel) wcbOptPanel.style.display = 'none';

        if (mainInputsRow) {
            mainInputsRow.classList.remove('form-row-5','form-row-6','form-row-7');
            mainInputsRow.classList.add('form-row-4');
            mainInputsRow.style.display = '';
        }

        if (val === 'second_candle') {
            if (scParamsRow) scParamsRow.style.display = 'grid';
            if (scLotRow)    scLotRow.style.display    = 'grid';
            // Base timeframe is selectable: 30second (~1 month of Fyers history)
            // or 1-minute and above (~10 years).
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '30second';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updateScInvestment();

        } else if (val === 'option_breakout') {
            if (obParamsRow) obParamsRow.style.display = 'grid';
            if (obLotRow)    obLotRow.style.display    = 'grid';
            // No timeframe picker: the rule IS a 30-second rule. Its "2nd
            // candle" is 09:15:30–09:16:00, and on any other bar size that
            // phrase names a different window — so a coarser bar would be a
            // different strategy, not a cheaper version of this one. The server
            // pins it too (_OB_INTERVAL); the select is still set so an earlier
            // strategy's choice can't leak into a shared payload.
            if (intFg) intFg.style.display = 'none';
            if (intervalSelect) intervalSelect.value = '30second';
            // The Start/End dates are the only thing that decides how much is
            // run. Two weeks by default: enough sessions to read, cheap enough
            // to be quick on a cold cache.
            const obEnd = document.getElementById('endDate');
            if (startDateInput) startDateInput.value = daysAgoLocal(14);
            if (obEnd)          obEnd.value          = localToday();
            updateObInvestment();

        } else if (val === 'expiry_breakout') {
            if (expParamsRow) expParamsRow.style.display = 'grid';
            // Pure filter — no symbol picking: scans every F&O stock over
            // the selected Start/End Date range and Timeframe.
            if (symFg) symFg.style.display = 'none';
            if (intFg) intFg.style.display = 'none';
            // Defaults to today only — the range is still editable, and a
            // wider one lists every past signal in it.
            const endDateInput = document.getElementById('endDate');
            if (startDateInput) startDateInput.value = localToday();
            if (endDateInput)   endDateInput.value   = localToday();

        } else if (val === 'thirty_min_fakeout') {
            if (tmfParamsRow) tmfParamsRow.style.display = 'grid';
            // Pure filter — no symbol picking: scans every F&O futures
            // stock/index. Always fetches 1-minute candles server-side —
            // the interval picker doesn't apply to this strategy.
            if (symFg) symFg.style.display = 'none';
            if (intFg) intFg.style.display = 'none';
            const endDateInput = document.getElementById('endDate');
            if (startDateInput) startDateInput.value = '2017-01-01';
            if (endDateInput)   endDateInput.value   = localToday();

        } else if (val === 'swing_momentum') {
            if (smParamsRow) smParamsRow.style.display = 'grid';
            if (symFg) symFg.style.display = 'none';
            if (intFg) intFg.style.display = 'none';
            if (startDateInput) startDateInput.value = '2017-01-01';

        } else if (val === 'ema_pullback') {
            if (emaParamsRow) emaParamsRow.style.display = 'grid';
            if (emaLotRow)    emaLotRow.style.display    = 'grid';
            // Daily-candle-only strategy — no intraday Timeframe to pick.
            if (intFg) intFg.style.display = 'none';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updateEmaInvestment();

        } else if (val === 'scalp_pullback') {
            if (spParamsRow) spParamsRow.style.display = 'grid';
            if (spLotRow)    spLotRow.style.display    = 'grid';
            // The video teaches this on 2-minute candles; 3/5 also work, so the
            // Timeframe picker stays visible.
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '2minute';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updateSpInvestment();

        } else if (val === 'pivot_confluence') {
            if (pcParamsRow) pcParamsRow.style.display = 'grid';
            if (pcLotRow)    pcLotRow.style.display    = 'grid';
            // The 9:30–9:45 morning window is only 15 minutes wide, so anything
            // finer than 5-minute candles is noise and anything coarser than
            // 15-minute leaves the window a single bar.
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) startDateInput.value = '2017-01-01';
            updatePcInvestment();

        } else if (val === 'simpler_trader') {
            // Index signal, ≈250 CE leg; lots/lot value reuse the Pivot Confluence boxes.
            // The Symbol box keeps whatever the last strategy left in it
            // (Weekly CPR Breakout parks ALL STOCKS there): this one trades one symbol —
            // an index, or an F&O stock on its ATM option.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (sbrParamsRow) sbrParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) {
                // 1 Hour: _sbr_load_bars resamples the stored minutes to any width, anchored on 09:15.
                if (![...intervalSelect.options].some(o => o.value === '60minute')) {
                    const opt = new Option('1 Hour', '60minute');
                    opt.dataset.hm = '1';       // removed again on the next strategy switch
                    intervalSelect.add(opt);
                }
                intervalSelect.value = '3minute';
            }
            if (startDateInput) startDateInput.value = '2019-04-01';
            if (val === 'simpler_trader') applySimplerTraderPreset();
            updatePcInvestment();

        } else if (val === 'hilega_milega') {
            // Index signal; optional CE/PE leg. Lots / lot value reuse the Pivot Confluence boxes.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (hmParamsRow) hmParamsRow.style.display = 'grid';
            if (pcLotRow)    pcLotRow.style.display    = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) {
                [['60minute', '1 Hour'], ['day', '1 Day']].forEach(([v, label]) => {
                    if (![...intervalSelect.options].some(o => o.value === v)) {
                        const opt = new Option(label, v);
                        opt.dataset.hm = '1';
                        intervalSelect.add(opt);
                    }
                });
                // A positional system: the talk uses the hourly chart (held two-three days) or the daily.
                [...intervalSelect.options].forEach(o => {
                    const ok = o.value === '60minute' || o.value === 'day';
                    o.hidden = !ok; o.disabled = !ok;
                });
                intervalSelect.value = '60minute';
            }
            if (startDateInput) startDateInput.value = '2022-01-01';
            updatePcInvestment();

        } else if (val === 'st_ema_rsi_cci') {
            // Index signal, ≈250 PE leg with a premium SL / TSL; lots / lot value reuse the Pivot Confluence boxes.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (secParamsRow) secParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '3minute';
            if (startDateInput) startDateInput.value = '2019-04-01';
            updatePcInvestment();

        } else if (val === 'st_rsi_pp_bb') {
            // Index signal, ≈250 CE leg with a premium SL / TSL; lots / lot value reuse the Pivot Confluence boxes.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (srpParamsRow) srpParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '3minute';
            if (startDateInput) startDateInput.value = '2019-04-01';
            updatePcInvestment();

        } else if (val === 'st_rsi_pp_bb_fut') {
            // NIFTY future signal on 5-minute candles, ≈250 CE leg with a premium SL / TSL.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (srfParamsRow) srfParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) startDateInput.value = '2019-04-01';
            updatePcInvestment();

        } else if (val === 'st_rsi_cci_wr_pp_bb_fut') {
            // NIFTY future signal on 3-minute candles, ≈250 CE leg with a premium SL / TSL.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (scwParamsRow) scwParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '3minute';
            if (startDateInput) startDateInput.value = '2019-04-01';
            updatePcInvestment();

        } else if (val === 'st_rsi_cpr_bb_sx') {
            // SENSEX index signal on 5-minute candles, ≈500 CE leg with a premium SL / TSL.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS || symbolSearch.value.toUpperCase() !== 'SENSEX') {
                selectedSymbol = 'SENSEX';
                symbolSearch.value = 'SENSEX';
            }
            if (scxParamsRow) scxParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            const lotVal = document.getElementById('pcLotValue');     // SENSEX's lot is 20, not NIFTY's 65
            if (lotVal) lotVal.value = 20;
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) startDateInput.value = '2023-10-24';
            updatePcInvestment();

        } else if (val === 'st_rsi_cpr_bb_wr_sx') {
            // SENSEX index signal on 5-minute candles, ≈500 CE leg (ST + RSI band + %R + narrow Traditional CPR + BB) with a premium SL / TSL.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS || symbolSearch.value.toUpperCase() !== 'SENSEX') {
                selectedSymbol = 'SENSEX';
                symbolSearch.value = 'SENSEX';
            }
            if (swxParamsRow) swxParamsRow.style.display = 'grid';
            if (pcLotRow)     pcLotRow.style.display     = 'grid';
            const lotVal = document.getElementById('pcLotValue');     // SENSEX's lot is 20, not NIFTY's 65
            if (lotVal) lotVal.value = 20;
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '5minute';
            if (startDateInput) startDateInput.value = '2023-10-24';
            updatePcInvestment();

        } else if (val === 'ma_htf') {
            // Index signal; optional CE/PE leg. Lots / lot value reuse the Pivot Confluence boxes.
            if (!symbolSearch.value || symbolSearch.value.toUpperCase() === ALL_STOCKS) {
                selectedSymbol = 'NIFTY';
                symbolSearch.value = 'NIFTY';
            }
            if (mhParamsRow) mhParamsRow.style.display = 'grid';
            if (pcLotRow)    pcLotRow.style.display    = 'grid';
            if (intFg) intFg.style.display = '';
            if (intervalSelect) intervalSelect.value = '3minute';
            if (startDateInput) startDateInput.value = '2024-01-01';
            updatePcInvestment();

        } else if (val === 'weekly_cpr_breakout') {
            if (wcbParamsRow) wcbParamsRow.style.display = 'grid';
            // The shared Symbol box, with ALL STOCKS (every F&O future stock)
            // first and selected by default, as EMA Confluence Breakout does.
            // The timeframe is fixed by the rule — a first HOUR read off
            // 1-minute bars — so the Timeframe picker does not apply.
            if (intFg) intFg.style.display = 'none';
            selectedSymbol = ALL_STOCKS;
            symbolSearch.value = ALL_STOCKS;
            // 2017 to today — as far back as the 1-minute store reaches.
            const wcbEnd = document.getElementById('endDate');
            if (startDateInput) startDateInput.value = '2017-01-01';
            if (wcbEnd) wcbEnd.value = localToday();
        } else if (val === 'ha_supertrend') {
            if (hastParamsRow) hastParamsRow.style.display = 'grid';
            // Daily candles, positional by rule: the Timeframe shows 1 Day and is locked.
            // ALL STOCKS = every F&O future stock.
            if (intFg) intFg.style.display = '';
            if (intervalSelect) {
                if (![...intervalSelect.options].some(o => o.value === 'day')) {
                    const opt = new Option('1 Day (positional)', 'day');
                    opt.dataset.hm = '1';
                    intervalSelect.add(opt);
                }
                [...intervalSelect.options].forEach(o => { const ok = o.value === 'day'; o.hidden = !ok; o.disabled = !ok; });
                intervalSelect.value = 'day';
                intervalSelect.disabled = true;
            }
            selectedSymbol = ALL_STOCKS;
            symbolSearch.value = ALL_STOCKS;
            const hastEnd = document.getElementById('endDate');
            if (startDateInput) startDateInput.value = '2019-01-01';
            if (hastEnd) hastEnd.value = localToday();
        } else if (val === 'option_selling') {
            if (osParamsRow) osParamsRow.style.display = 'grid';
            // NIFTY only, on a fixed 1-minute basis: no Symbol / Timeframe.
            if (symFg) symFg.style.display = 'none';
            if (intFg) intFg.style.display = 'none';
            selectedSymbol = 'NIFTY';
            symbolSearch.value = 'NIFTY';
            // A year back by default — ICICI's expired-contract candles do not reach further reliably.
            const osEnd = document.getElementById('endDate');
            const osStart = new Date(); osStart.setFullYear(osStart.getFullYear() - 1);
            if (startDateInput) startDateInput.value = osStart.toISOString().slice(0, 10);
            if (osEnd) osEnd.value = localToday();
        }

        // "All Stocks" only exists for EMA Confluence Breakout — every other
        // strategy backtests one symbol, so switching away falls back to the
        // default rather than posting a symbol the backend can't resolve.
        if (val !== 'ema_pullback' && val !== 'weekly_cpr_breakout' && val !== 'ha_supertrend' && selectedSymbol === ALL_STOCKS) {
            selectedSymbol = 'NIFTY';
            symbolSearch.value = selectedSymbol;
            applyLotValueForSymbol(selectedSymbol);
            applyEmaDefaultsForSymbol(selectedSymbol);
        }
        syncEmaAllStocksMode();
    }

    // Investment display: ₹50,000 per lot. Defined BEFORE the initial
    // updateStrategyView() call below — whichever strategy is the default
    // `selected` option in the HTML calls its own update*Investment()
    // immediately on load, so these must already exist by then.
    window.updateScInvestment = function() {
        const lots  = Math.max(1, parseInt(document.getElementById('scLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('scInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // 30-Sec Option Breakout investment display
    window.updateObInvestment = function() {
        const lots  = Math.max(1, parseInt(document.getElementById('obLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('obInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // 2-Min EMA Scalp Pullback investment display
    window.updateSpInvestment = function() {
        const lots  = Math.max(1, parseInt(document.getElementById('spLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('spInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // Pivot Confluence investment display
    window.updatePcInvestment = function() {
        const lots  = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('pcInvestmentDisplay');
        if (el) el.textContent = '₹' + total.toLocaleString('en-IN');
    };

    // EMA Confluence Breakout investment display
    // `actual` is the average ₹ a completed run actually deployed per entry
    // (futures fill × lots × lot size) — the real figure, once there is one.
    window.updateEmaInvestment = function(actual) {
        const lots  = Math.max(1, parseInt(document.getElementById('emaLots')?.value || 1));
        const total = lots * 50000;
        const el = document.getElementById('emaInvestmentDisplay');
        if (!el) return;
        // Across All Stocks each entry is lots × THAT stock's lot size ×
        // its own price, so there's no single figure to show up front —
        // the results' Net P&L card reports the real average per entry.
        if (isEmaAllMode()) { el.textContent = 'varies by stock'; return; }
        el.textContent = '₹' + Math.round(actual || total).toLocaleString('en-IN');
    };

    // ── Status filter: narrows the strategy dropdown to one maturity bucket ──
    const strategyStatusSelect = document.getElementById('strategyStatusSelect');
    // "live" is every strategy that also runs as a live algo thread — the
    // Candle Breakout (paper-only since 2026-09-15, but still a running
    // thread), 30-Min Fakeout, Swing Momentum (Go Live / Live Watch) and EMA
    // Confluence, and ST + BB + RSI (3 index algos, paper by default). A strategy is in exactly one bucket: when one goes live,
    // MOVE it here rather than copying it.
    const STRATEGY_STATUS = {
        simpler: ['simpler_trader', 'st_ema_rsi_cci', 'st_rsi_pp_bb', 'st_rsi_pp_bb_fut', 'st_rsi_cci_wr_pp_bb_fut', 'st_rsi_cpr_bb_sx', 'st_rsi_cpr_bb_wr_sx'],
        live:    ['second_candle', 'thirty_min_fakeout', 'swing_momentum', 'ema_pullback'],
        success: ['option_breakout', 'expiry_breakout'],
        testing: ['scalp_pullback', 'pivot_confluence', 'weekly_cpr_breakout', 'option_selling', 'hilega_milega', 'ma_htf', 'ha_supertrend'],
        failure: [],
    };
    // Keep every <option> node around — filtering re-appends from this list, so
    // switching status back restores the original labels and order.
    const _allStrategyOptions = strategySelect
        ? Array.from(strategySelect.options)
        : [];

    function applyStrategyStatusFilter() {
        if (!strategySelect || !strategyStatusSelect) return;
        const allowed = STRATEGY_STATUS[strategyStatusSelect.value] || [];
        const prev    = strategySelect.value;

        strategySelect.innerHTML = '';
        _allStrategyOptions
            .filter(opt => allowed.includes(opt.value))
            .forEach(opt => strategySelect.appendChild(opt));

        if (!strategySelect.options.length) return;
        // Selection survives only if the strategy is still in the bucket;
        // otherwise fall back to the first one and rebuild the form for it.
        if (allowed.includes(prev)) {
            strategySelect.value = prev;
        } else {
            strategySelect.selectedIndex = 0;
            updateStrategyView();
            updateLiveFlagBadge();
        }
    }

    if (strategyStatusSelect) {
        strategyStatusSelect.addEventListener('change', applyStrategyStatusFilter);
        applyStrategyStatusFilter();
    }

    // ST + BB + RSI "Option Premium ≈" choices follow the symbol: NIFTY-sized by default,
    // BANKNIFTY centred on 500, SENSEX on 800. The symbol box is also set from code
    // (no event), so a light poll catches every change.
    const SBR_PREMIUMS = {
        DEFAULT:   { list: [150, 200, 250, 300], def: 250 },
        BANKNIFTY: { list: [300, 400, 500, 600], def: 500 },
        SENSEX:    { list: [500, 650, 800, 1000], def: 800 },
    };
    let _sbrPremiumKey = null;
    function syncSbrPremium() {
        const sel = document.getElementById('sbrPremium');
        if (!sel || !symbolSearch) return;
        const sym = (symbolSearch.value || '').toUpperCase();
        const key = sym.includes('BANKNIFTY') ? 'BANKNIFTY' : sym.includes('SENSEX') ? 'SENSEX' : 'DEFAULT';
        if (key === _sbrPremiumKey) return;
        _sbrPremiumKey = key;
        const cfg = SBR_PREMIUMS[key];
        sel.innerHTML = cfg.list.map(v => `<option value="${v}"${v === cfg.def ? ' selected' : ''}>${v}</option>`).join('');
        if (strategySelect && strategySelect.value === 'simpler_trader') applySimplerTraderPreset();
    }
    syncSbrPremium();
    setInterval(syncSbrPremium, 400);

    if (strategySelect) {
        strategySelect.addEventListener('change', updateStrategyView);
        // Set initial state
        updateStrategyView();
    }


    // ── Backtest logic popup (💡 Logic button, explains the selected strategy) ──
    const BT_LOGIC = {
        second_candle: { sub: 'Index breakout of the 2nd 30-second candle', steps: [
            ['Range', 'Take the 2nd 30-second candle of the day (09:15:30–09:16:00). Its High and Low are the range.'],
            ['Entry', 'Price goes above the High → BUY. Price goes below the Low → SELL. Only the first breakout of the day is taken.'],
            ['Stop', 'The opposite end of that candle (Low for a BUY, High for a SELL).'],
            ['Target', 'Entry plus the chosen ratio × risk (default 1 : 3).'],
            ['Exit', 'Stop, target, or the cut-off time (default 15:25) — whichever comes first. One trade per day.']],
            note: 'Signals are always on the index. With the option P&L switch, profit is shown on a modelled CE/PE premium instead of index points.' },
        option_breakout: { sub: 'Same idea, but on the option\'s own candles', steps: [
            ['Strike', 'At the open, pick the strike nearest to NIFTY\'s open. Watch both its CE and its PE.'],
            ['Range', 'Each option\'s own 2nd 30-second candle gives its High and Low.'],
            ['Entry', 'When that option\'s premium breaks above its candle High, BUY it at the High. CE and PE are independent — both can trade on one day.'],
            ['Stop / Target', 'Stop = candle Low minus a small buffer (₹1). Target = entry + ratio × risk.'],
            ['Exit', 'Stop, target, cut-off time or end of day. Each option trades at most once a day.']],
            note: 'All prices and P&L are real option premiums (needs a Breeze login for old dates).' },
        expiry_breakout: { sub: 'Monthly expiry-day High/Low breakout on 1-hour candles', steps: [
            ['Levels', 'Take the High and Low of each monthly expiry day.'],
            ['Signal (BUY)', 'A 1-hour candle touches the expiry High and CLOSES above it, touches at least one of the 20/50/100/200 EMAs, and closes above all four.'],
            ['Signal (SELL)', 'The exact mirror: closes below the expiry Low and below all four EMAs.'],
            ['Entry', 'At the open of the next 1-hour candle.'],
            ['Stop / Target', 'Stop is the expiry Low (BUY) or High (SELL). Target uses your chosen ratio.'],
            ['Repeat', 'After an exit, scanning starts again — a cycle can have several trades.']] },
        thirty_min_fakeout: { sub: 'Opening 30-minute fake breakout that reverses', steps: [
            ['Setup (SELL)', 'Candle 1 green; candle 2 green and closes above candle 1\'s High. Candle 3 pokes above candle 2\'s High but closes back below it (a fakeout), without closing too deep and with a dominant upper wick.'],
            ['Entry', 'After 10:45, SELL when price breaks below candle 3\'s Low (minus a 0.05 % buffer).'],
            ['Stop', 'Candle 3\'s High.'],
            ['Target', 'The day\'s low (backtest-only; known in hindsight).'],
            ['BUY side', 'Exact mirror image: red, red, then a fake break below that closes back above.'],
            ['Exit', 'Stop, target, or cut-off (default 15:18).']] },
        swing_momentum: { sub: 'Monthly momentum stock portfolio', steps: [
            ['Rank', 'Rank every stock in the chosen index by average momentum over 3, 6 and 9 months.'],
            ['Buy', 'Hold the Top N stocks, equal weight.'],
            ['Rebalance', 'On each weekly/monthly/quarterly date, sell stocks that fell below the exit rank and buy the new leaders.'],
            ['Result', 'Portfolio value, CAGR and max drawdown over time. Optional monthly top-up money.']] },
        ema_pullback: { sub: 'Daily candle that touches all four EMAs', steps: [
            ['Signal', 'A daily candle whose range touches EMA 20, 50, 100 and 200 at once. Green = BUY setup, red = SELL setup.'],
            ['Entry', 'Break of that candle\'s High (BUY) or Low (SELL) on a later day. A newer signal replaces an unfilled older one.'],
            ['Stop', 'The signal candle\'s opposite end.'],
            ['Target', 'A % of the entry price (default 5 %).'],
            ['Filter', 'Optionally skip trades where the target pays less than the stop risks.']] },
        scalp_pullback: { sub: '2-minute trend pullback with candle patterns', steps: [
            ['Bias', 'EMA10 above EMA20 with price above both → longs only. The reverse → shorts only.'],
            ['Trend', 'The trend must have been running for a while and made a decent move first.'],
            ['Pullback', 'Price comes back to touch the EMA, usually after sweeping a recent swing low/high (stop hunt).'],
            ['Trigger', 'A Morning Star / Bullish Engulfing (long) or Evening Star / Bearish Engulfing (short). Dojis and huge candles are skipped.'],
            ['Entry / Stop', 'Stop-entry beyond the pattern\'s extreme; stop at its opposite end; too-wide stops are skipped.']] },
        pivot_confluence: { sub: '"Yesterday decides today" — pivots + VWAP + EMA', steps: [
            ['Direction', 'Compute pivots from yesterday\'s H/L/C. Above the pivot → calls only; below → puts only.'],
            ['Day bias', 'A full candle body beyond yesterday\'s High (up) or Low (down).'],
            ['Confluence', '20 EMA and VWAP agree with the direction, price on the right side of the 20 EMA.'],
            ['Timing', 'Enter after 09:30, plus an optional second window in the afternoon; max 2 trades a day.'],
            ['Target', 'The next pivot level (R1/R2… or S1/S2…). Entries already parked at R2/S2 are refused.']] },
        simpler_trader: { sub: 'Quantman "BB PP ST RSI WR with SL" — long NIFTY CE, 3-minute candles', steps: [
            ['Entry rule', 'On a closed 3-min candle ALL must hold: close above SuperTrend(10, 2), RSI(14) above 70, close above R1 of the traditional day pivots, close above the upper Bollinger Band (15 candles, multiplier 3), and Williams %R(14) above -30.'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week CE whose premium is nearest 250.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '13% under the entry premium; trailing: for every 15% (X) the premium rises, the stop moves up 10% (Y) of the entry premium.'],
            ['Limit', 'Trade window 09:15-15:14, one transaction per day.']] },
        st_rsi_pp_bb: { sub: 'Quantman "ST RSI PP BB with SL" — long NIFTY CE, 3-minute candles on the index', steps: [
            ['Entry rule', 'On a closed 3-min candle of the NIFTY index ALL must hold: close above SuperTrend(10, 2.3), RSI(14) above 70, close above R1 of the traditional day pivots, and close above the upper Bollinger Band (15 candles, multiplier 3).'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week CE whose premium is nearest 250.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '15% under the entry premium; trailing: for every 15% (X) the premium rises, the stop moves up 10% (Y) of the entry premium.'],
            ['Re-entry', 'If the option\'s own stop ends a trade, the next candle that still signals buys again (Next Minute).'],
            ['Limit', 'Trade window 09:31-15:14, up to 5 transactions a day, one open at a time.']] },
        st_rsi_pp_bb_fut: { sub: 'Quantman "ST RSI PP BB with SL NIFTY Fut CE" — long NIFTY CE, 5-minute candles on the future', steps: [
            ['Entry rule', 'On a closed 5-min candle of the NIFTY future ALL must hold: close above SuperTrend(10, 1.8), RSI(20) above 68, close above R1 of the traditional day pivots, close above the upper Bollinger Band (20 candles, multiplier 3.3), and close above the candle\'s own open.'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week CE whose premium is nearest 250.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '13% under the entry premium; trailing: for every 13% (X) the premium rises, the stop moves up 10% (Y) of the entry premium.'],
            ['Re-entry', 'If the option\'s own stop ends a trade, the next candle that still signals buys again (Next Minute).'],
            ['Limit', 'Trade window 09:15-15:14, up to 5 transactions a day, one open at a time.']] },
        st_rsi_cpr_bb_sx: { sub: 'Quantman "ST RSI CPR BB with SL Sensex CE" — long SENSEX CE, 5-minute candles on the index', steps: [
            ['Entry rule', 'On a closed 5-min candle of the SENSEX index ALL must hold: close above SuperTrend(10, 1.5), RSI(14) above 70, close above R1 of today\'s Demark CPR, that CPR not wide (width under 0.35 %), and close above the upper Bollinger Band (20 candles, multiplier 2.5).'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week SENSEX CE whose premium is nearest 500.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '17% under the entry premium; trailing: for every 15% (X) the premium rises, the stop moves up 10% (Y) of the entry premium.'],
            ['Re-entry', 'If the option\'s own stop ends a trade, the next candle that still signals buys again (Next Minute).'],
            ['Limit', 'Trade window 09:15-15:14, up to 5 transactions a day, one open at a time.']] },
        st_rsi_cpr_bb_wr_sx: { sub: 'Quantman "ST RSI CPR BB WR with SL SX2 CE" — long SENSEX CE, 5-minute candles on the index', steps: [
            ['Entry rule', 'On a closed 5-min candle of the SENSEX index ALL must hold: close above SuperTrend(10, 1.5), RSI(14) above 68 and below 80, Williams %R(14) above -10, close above R1 of today\'s Traditional CPR, that CPR narrow (width under 0.2 %), and close above the upper Bollinger Band (20 candles, multiplier 2.5).'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week SENSEX CE whose premium is nearest 500.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '15% under the entry premium; trailing: for every 15% (X) the premium rises, the stop moves up 10% (Y) of the entry premium.'],
            ['Re-entry', 'If the option\'s own stop ends a trade, the next candle that still signals buys again (Next Minute).'],
            ['Limit', 'Trade window 09:15-15:14, up to 5 transactions a day, one open at a time.']] },
        st_rsi_cci_wr_pp_bb_fut: { sub: 'Quantman "ST RSI CCI WR PP BB With SL NIFTY Fut CE" — long NIFTY CE, 3-minute candles on the future', steps: [
            ['Entry rule', 'On a closed 3-min candle of the NIFTY future ALL must hold: close above SuperTrend(10, 2), RSI(14) above 78, CCI(20) above 280, Williams %R(14) above -15, close above R1 of the traditional day pivots, and close above the upper Bollinger Band (20 candles, multiplier 3).'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week CE whose premium is nearest 250.'],
            ['Exit', 'A candle closes below SuperTrend, the option\'s stop-loss is hit, or the 15:14 square-off.'],
            ['Stop-loss', '15% under the entry premium; no trailing stop on this run.'],
            ['Re-entry', 'If the option\'s own stop ends a trade, the next candle that still signals buys again (Next Minute).'],
            ['Limit', 'Trade window 09:15-15:14, up to 5 transactions a day, one open at a time.']] },
        st_ema_rsi_cci: { sub: 'Quantman "ST EMA RSI CCI WR CPR BB with SL" — short NIFTY PE, 3-minute candles', steps: [
            ['Entry rule', 'On a closed 3-min candle ALL must hold: close below SuperTrend(10, 2) and EMA(200), RSI(20) below 30, CCI(20) below -200, Williams %R(14) below -85, close below the lower Bollinger Band(20, 2), close below S1 of today\'s CPR, and that CPR is narrow (width under 0.2 %).'],
            ['Entry', 'At the NEXT candle\'s open: buy 1 lot of the current-week PE whose premium is nearest 250.'],
            ['Exit', 'A candle closes with RSI(20) above 45, the option\'s stop-loss or trailing stop is hit, or the 15:14 square-off.'],
            ['Stop-loss', '15 % under the entry premium; trailing: for every 5 % (X) the premium rises, the stop moves up 5 % (Y) of the entry premium.'],
            ['Limit', 'Trade window 09:15-15:14, up to 5 transactions a day, one open at a time.']] },
        hilega_milega: { sub: 'RSI(9) with a green EMA(3) and red volume-weighted MA(21) on the RSI', steps: [
            ['Bull zone', 'RSI above 50, the red line above 50 and under the RSI. (Bear zone is the mirror.)'],
            ['Signal', 'Once the zone turns on, the first candle that closes beyond the previous candle\'s High (long) / Low (short) within the window.'],
            ['Entry', 'Next candle\'s open.'],
            ['Stop', 'The signal candle\'s Low (long) / High (short), usually on a candle CLOSE.'],
            ['Exit', 'Stop, optional target, red line leaving the zone (or RSI dropping to green line), or the square-off unless positional.']] },
        ma_htf: { sub: '11/21 moving-average cross, only with the higher-timeframe trend', steps: [
            ['Cross', 'Fast SMMA(11) crosses slow SMMA(21).'],
            ['Filter', 'Only take it if price is on the same side of the higher-timeframe MA(21).'],
            ['Confirm', 'Within a few candles, a candle closes beyond all three lines with the cross still intact. Otherwise the move is skipped as already run.'],
            ['Entry', 'Next candle\'s open (not before 09:30).'],
            ['Exit', 'Fixed stop in points, opposite cross, or square-off. Optional trailing stop and partial booking.']] },
        weekly_cpr_breakout: { sub: 'Stock opens inside weekly CPR, then breaks the first-hour candle', steps: [
            ['Setup', 'The day opens inside the weekly CPR; the CPR is narrow; the first hour is a decent-sized candle that is not already at weekly R3/S3.'],
            ['Direction', 'Red first-hour candle → SELL, green → BUY.'],
            ['Entry', 'After 10:15, when price breaks the first-hour candle\'s High (BUY) or Low (SELL).'],
            ['Stop / Target', 'Stop = candle\'s other end. Target = ratio × the candle\'s range.'],
            ['Exit', 'Stop (wins if both touched in one bar), target, or 15:20. One trade per stock per day.']] },
        ha_supertrend: { sub: 'Daily Heikin-Ashi + SuperTrend swing', steps: [
            ['Signal', 'SuperTrend (10,3) on Heikin-Ashi candles has just flipped to your side, and the HA candle is your colour.'],
            ['Entry', 'Next day\'s open.'],
            ['Stop', 'Recent swing low (long) / high (short). Too-wide stops are skipped.'],
            ['Target', 'Ratio × risk — a part of the position is sold there.'],
            ['Trail', 'The rest is held until SuperTrend flips back, two opposite HA candles appear, or the stop is hit.']] },
        option_selling: { sub: 'NIFTY weekly short strangle', steps: [
            ['Days', 'Monday, Tuesday and Friday (configurable).'],
            ['Entry', 'At 09:30, SELL one OTM CE and one OTM PE of the current weekly expiry.'],
            ['Stop', 'Each leg has its own stop, a % above its entry premium (e.g. 60 %).'],
            ['Re-entry', 'If a leg stops out, sell it again once when its premium returns to the original entry.'],
            ['Exit', 'No target. Anything still open is bought back at 10:30.']] },
    };
    const btLogicBtn   = document.getElementById('btLogicBtn');
    const btLogicModal = document.getElementById('btLogicModal');
    if (btLogicBtn && btLogicModal && strategySelect) {
        document.body.appendChild(btLogicModal);
        const esc = (t) => String(t).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
        let prevOverflow = '';
        const closeLogic = () => {
            btLogicModal.style.display = 'none';
            document.body.style.overflow = prevOverflow;
        };
        btLogicBtn.addEventListener('click', () => {
            const key  = strategySelect.value;
            const info = BT_LOGIC[key];
            const name = strategySelect.options[strategySelect.selectedIndex]?.textContent || key;
            document.getElementById('btLogicTitle').textContent = name + ' — Logic';
            document.getElementById('btLogicSub').textContent = info ? info.sub : '';
            document.getElementById('btLogicBody').innerHTML = info
                ? '<ol style="margin:0;padding-left:20px;line-height:1.6;font-size:14px;">'
                    + info.steps.map(s => `<li style="margin-bottom:8px;"><b>${esc(s[0])}:</b> ${esc(s[1])}</li>`).join('')
                    + '</ol>'
                    + (info.note ? `<div style="margin-top:10px;font-size:12.5px;opacity:.8;">ℹ️ ${esc(info.note)}</div>` : '')
                : '<p>No explanation written for this strategy yet.</p>';
            btLogicModal.style.display = 'flex';
            prevOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        });
        document.getElementById('btLogicClose').addEventListener('click', closeLogic);
        btLogicModal.addEventListener('click', (e) => { if (e.target === btLogicModal) closeLogic(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && btLogicModal.style.display !== 'none') closeLogic();
        });
    }

    // ── 30-Min Opening Fakeout: Rule popup ─────────────────────────────
    const tmfRuleTrigger = document.getElementById('tmfRuleTrigger');
    const tmfRuleModal   = document.getElementById('tmfRuleModal');
    const tmfRuleClose   = document.getElementById('tmfRuleModalClose');
    if (tmfRuleTrigger && tmfRuleModal) {
        // Re-parent to <body> so the overlay's `position:fixed` is always
        // relative to the real viewport — safe against any ancestor ever
        // gaining a transform/filter/contain, which would otherwise turn
        // "fixed" into "fixed relative to that ancestor" and let the popup
        // scroll away with the page instead of staying put on screen.
        document.body.appendChild(tmfRuleModal);

        let _tmfPrevBodyOverflow = '';
        const openTmfRule = () => {
            tmfRuleModal.style.display = 'flex';
            // Lock background scroll while open so the page underneath can't
            // scroll out from under the overlay.
            _tmfPrevBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        };
        const closeTmfRule = () => {
            tmfRuleModal.style.display = 'none';
            document.body.style.overflow = _tmfPrevBodyOverflow;
        };
        tmfRuleTrigger.addEventListener('click', openTmfRule);
        if (tmfRuleClose) tmfRuleClose.addEventListener('click', closeTmfRule);
        tmfRuleModal.addEventListener('click', (e) => { if (e.target === tmfRuleModal) closeTmfRule(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && tmfRuleModal.style.display !== 'none') closeTmfRule();
        });
    }

    // ── EMA Confluence Breakout: Rule popup ──────────────────────────────
    const emaRuleTrigger = document.getElementById('emaRuleTrigger');
    const emaRuleModal   = document.getElementById('emaRuleModal');
    const emaRuleClose   = document.getElementById('emaRuleModalClose');
    if (emaRuleTrigger && emaRuleModal) {
        document.body.appendChild(emaRuleModal);
        let _emaPrevBodyOverflow = '';
        const openEmaRule = () => {
            emaRuleModal.style.display = 'flex';
            _emaPrevBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        };
        const closeEmaRule = () => {
            emaRuleModal.style.display = 'none';
            document.body.style.overflow = _emaPrevBodyOverflow;
        };
        emaRuleTrigger.addEventListener('click', openEmaRule);
        if (emaRuleClose) emaRuleClose.addEventListener('click', closeEmaRule);
        emaRuleModal.addEventListener('click', (e) => { if (e.target === emaRuleModal) closeEmaRule(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && emaRuleModal.style.display !== 'none') closeEmaRule();
        });
    }

    // 30-Min Opening Fakeout investment display
    // 3. Run Backtest
    runBtn.addEventListener('click', async function() {
        const _strat = strategySelect ? strategySelect.value : '';
        if (_strat === 'expiry_breakout') {
            await runExpiryScan();
            return;
        }
        // Backgrounded on the server — see the endpoint's own note. It reports
        // progress instead of blocking, so it cannot ride the shared
        // fetch-and-render path below.
        if (_strat === 'option_breakout') {
            await runOptionBreakout();
            return;
        }
        if (_strat === 'option_selling') {
            await runOptionSelling();
            return;
        }
        // Also backgrounded: every F&O stock, minutes on a cold cache.
        if (_strat === 'weekly_cpr_breakout') {
            await runWeeklyCprBreakout();
            return;
        }
        if (_strat === 'ha_supertrend') {
            await runHaSupertrend();
            return;
        }
        const symbol = symbolSearch.value.toUpperCase();
        if (_strat !== 'swing_momentum' && _strat !== 'thirty_min_fakeout' && !symbol) {
            window.showNotification('Please select a symbol', 'warning');
            return;
        }

        const payload = {
            symbol: symbol,
            start_date: document.getElementById('startDate').value,
            end_date: document.getElementById('endDate').value,
            interval: document.getElementById('interval').value,
        };

        _hideOptLoader();                 // drop any optimiser progress text first
        loading.style.display = 'flex';   // flex so the loader card centres (see .bt-loading)
        resultsArea.style.display = 'none';
        const btTradesSec   = document.getElementById('btTradesSection');
        const btPlaceholder = document.getElementById('btRightPlaceholder');
        const periodSec     = document.getElementById('periodBreakdownSection');
        const smOptPanel    = document.getElementById('smOptimisePanel');
        const tmfOptPanel2  = document.getElementById('thirtyMinFakeoutOptimisePanel');
        if (btTradesSec)    btTradesSec.style.display    = 'none';
        if (btPlaceholder)  btPlaceholder.style.display  = 'none';
        if (periodSec)      periodSec.style.display      = 'none';
        // Keep the Candle Breakout / Scalp / Pivot "Best Params" panels visible
        // across a backtest run, but collapse their grids so the results take focus.
        setCollapsed(document.querySelector('#secondCandleOptimisePanel .opt-header'), true);
        setCollapsed(document.querySelector('#optionBreakoutOptimisePanel .opt-header'), true);
        setCollapsed(document.querySelector('#scalpPullbackOptimisePanel .opt-header'), true);
        setCollapsed(document.querySelector('#pivotConfluenceOptimisePanel .opt-header'), true);
        if (smOptPanel)     smOptPanel.style.display     = 'none';
        if (tmfOptPanel2)   tmfOptPanel2.style.display   = 'none';

        try {
            const strat = strategySelect ? strategySelect.value : '';
            let endpoint = null;

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
                // P&L basis: unticked = NIFTY index points, ticked = the
                // premium of the CE/PE leg the live algo would have bought.
                payload.option_pnl   = document.getElementById('scOptionPnl')?.checked ?? false;
            }

            // 2-Min EMA Scalp Pullback
            if (strat === 'scalp_pullback') {
                endpoint = '/api/backtest/scalp-pullback';
                payload.interval      = document.getElementById('interval')?.value || '2minute';
                payload.touch_level   = document.getElementById('spTouchLevel')?.value || 'ema20';
                payload.rr_ratio      = parseFloat(document.getElementById('spRrRatio')?.value || 2);
                // 0 is meaningful here — it lifts the stop-width ceiling entirely.
                payload.max_sl_points = parseFloat(document.getElementById('spMaxSl')?.value ?? 18);
                payload.max_trades_per_day = parseInt(document.getElementById('spMaxTrades')?.value || 3);
                payload.entry_cutoff  = document.getElementById('spEntryCutoff')?.value || '11:30';
                payload.exit_cutoff   = document.getElementById('spExitCutoff')?.value || '15:15';
                payload.direction     = document.getElementById('spDirection')?.value || 'both';
                payload.require_sl_hunt = document.getElementById('spRequireSlHunt')?.checked ?? true;
                payload.first_trade_three_candle = document.getElementById('spFirstThreeCandle')?.checked ?? true;
                const spTrail = document.getElementById('spTrailSl')?.value;
                if (spTrail) payload.trail_points = parseFloat(spTrail);
            }

            // Pivot Confluence (yesterday's pivots + YH/YL + VWAP/20 EMA)
            if (strat === 'pivot_confluence') {
                endpoint = '/api/backtest/pivot-confluence';
                payload.interval       = document.getElementById('interval')?.value || '5minute';
                payload.entry_trigger  = document.getElementById('pcTrigger')?.value || 'either';
                payload.confluence     = document.getElementById('pcConfluence')?.value || 'vwap_ema';
                payload.target_mode    = document.getElementById('pcTargetMode')?.value || 'next_level';
                payload.rr_ratio       = parseFloat(document.getElementById('pcRrRatio')?.value || 2);
                payload.target_points  = parseFloat(document.getElementById('pcTargetPoints')?.value || 0);
                payload.sl_mode        = document.getElementById('pcSlMode')?.value || 'candle';
                payload.sl_points      = parseFloat(document.getElementById('pcSlPoints')?.value || 0);
                // 0 is meaningful here — it lifts the stop-width ceiling entirely.
                payload.max_sl_points  = parseFloat(document.getElementById('pcMaxSl')?.value || 0);
                payload.entry_windows  = document.getElementById('pcWindows')?.value || 'both';
                // An empty Morning Exit means "let the morning trade run".
                payload.morning_exit   = document.getElementById('pcMorningExit')?.value || '';
                payload.exit_cutoff    = document.getElementById('pcExitCutoff')?.value || '15:15';
                payload.max_trades_per_day = parseInt(document.getElementById('pcMaxTrades')?.value || 2);
                payload.fib_ext        = parseFloat(document.getElementById('pcFibExt')?.value || 0);
                payload.direction      = document.getElementById('pcDirection')?.value || 'both';
                payload.full_body      = document.getElementById('pcFullBody')?.checked ?? true;
                payload.require_no_wick = document.getElementById('pcNoWick')?.checked ?? false;
                payload.block_beyond_r2 = document.getElementById('pcBlockR2')?.checked ?? true;
                payload.require_golden_cross = document.getElementById('pcGoldenCross')?.checked ?? false;
                const pcTrail = document.getElementById('pcTrailSl')?.value;
                if (pcTrail) payload.trail_points = parseFloat(pcTrail);
            }

            // SuperTrend + BB + RSI + R1, bought as the ≈250 CE; exit on SuperTrend break
            if (strat === 'simpler_trader') {
                endpoint = '/api/backtest/st-bb-rsi';
                // An index buys its ≈premium weekly option; an F&O stock buys the ATM monthly one
                // (the server prices it and ignores the premium box). ALL STOCKS is not one symbol.
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                payload.interval      = document.getElementById('interval')?.value || '3minute';
                payload.premium       = parseFloat(document.getElementById('sbrPremium')?.value || 250);
                payload.price_options = document.getElementById('sbrPriceOptions')?.checked ?? true;
                payload.rsi_period    = parseInt(document.getElementById('sbrRsiLen')?.value || 14);
                payload.st_period     = parseInt(document.getElementById('sbrStLen')?.value || 10);
                payload.st_mult       = parseFloat(document.getElementById('sbrStFactor')?.value || 2);
                payload.bb_period     = parseInt(document.getElementById('sbrBbLen')?.value || 20);
                payload.bb_mult       = parseFloat(document.getElementById('sbrBbMult')?.value || 2);
                payload.bb_offset     = parseInt(document.getElementById('sbrBbOffset')?.value || 0);
                payload.direction     = document.getElementById('sbrDirection')?.value || 'both';
                payload.htf           = document.getElementById('sbrHtf')?.value || 'off';
                payload.lots          = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value     = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding   = document.getElementById('sbrCompound')?.checked ?? false;
                payload.start_capital = parseFloat(document.getElementById('sbrCapital')?.value || 25000);
                payload.per_lot       = parseFloat(document.getElementById('sbrPerLot')?.value || 0);
                payload.require_green = document.getElementById('sbrGreen')?.checked ?? false;
                payload.use_rsi       = document.getElementById('sbrUseRsi')?.checked ?? true;
                payload.use_bb        = document.getElementById('sbrUseBb')?.checked ?? true;
                payload.use_st        = document.getElementById('sbrUseSt')?.checked ?? true;
                payload.use_pivot     = document.getElementById('sbrUsePivot')?.checked ?? true;
                payload.use_wr        = document.getElementById('sbrUseWr')?.checked ?? false;
                payload.wr_period     = parseInt(document.getElementById('sbrWrLen')?.value || 14);
                payload.wr_level      = parseFloat(document.getElementById('sbrWrLevel')?.value ?? -30);
                payload.sl_pct        = parseFloat(document.getElementById('sbrSlPct')?.value || 0);
                payload.tsl_x         = parseFloat(document.getElementById('sbrTslX')?.value || 0);
                payload.tsl_y         = parseFloat(document.getElementById('sbrTslY')?.value || 0);
                payload.rsi_level     = parseFloat(document.getElementById('sbrRsi')?.value || 70);
                payload.rsi_lower     = parseFloat(document.getElementById('sbrRsiLower')?.value || 30);
                payload.entry_start   = document.getElementById('sbrEntryStart')?.value || '09:15';
                payload.entry_end     = document.getElementById('sbrEntryEnd')?.value || '15:14';
                payload.exit_cutoff   = document.getElementById('sbrExitCutoff')?.value || '15:14';
                payload.max_trades_per_day = parseInt(document.getElementById('sbrMaxTrades')?.value || 2);
            }

            // ST + EMA + RSI + CCI + WR + CPR + BB: short index signal, bought as the ≈250 PE with a premium SL / TSL
            if (strat === 'st_ema_rsi_cci') {
                endpoint = '/api/backtest/st-ema-rsi-cci';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '3minute');
                payload.premium        = parseFloat(sev('secPremium', 250));
                payload.price_options  = document.getElementById('secPriceOptions')?.checked ?? true;
                payload.direction      = sev('secDirection', 'short');
                payload.st_period      = sen('secStLen', 10);
                payload.st_mult        = sen('secStFactor', 2);
                payload.ema_period     = sen('secEmaLen', 200);
                payload.rsi_period     = sen('secRsiLen', 20);
                payload.rsi_entry      = sen('secRsiEntry', 30);
                payload.rsi_exit       = sen('secRsiExit', 45);
                payload.cci_period     = sen('secCciLen', 20);
                payload.cci_level      = sen('secCciLevel', -200);
                payload.wr_period      = sen('secWrLen', 14);
                payload.wr_level       = sen('secWrLevel', -85);
                payload.bb_period      = sen('secBbLen', 20);
                payload.bb_mult        = sen('secBbMult', 2);
                payload.cpr_narrow     = sen('secCprNarrow', 0.2);
                payload.use_cpr_narrow = document.getElementById('secUseCprNarrow')?.checked ?? true;
                payload.sl_pct         = sen('secSlPct', 0);
                payload.tsl_x          = sen('secTslX', 0);
                payload.tsl_y          = sen('secTslY', 0);
                payload.entry_start    = sev('secEntryStart', '09:15');
                payload.entry_end      = sev('secEntryEnd', '15:14');
                payload.exit_cutoff    = sev('secExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('secMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding    = document.getElementById('secCompound')?.checked ?? false;
                payload.start_capital  = sen('secCapital', 25000);
                payload.per_lot        = sen('secPerLot', 0);
            }

            // ST + RSI + PP + BB: long index signal, bought as the ≈250 CE with a premium SL / TSL
            if (strat === 'st_rsi_pp_bb') {
                endpoint = '/api/backtest/st-rsi-pp-bb';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '3minute');
                payload.premium        = parseFloat(sev('srpPremium', 250));
                payload.price_options  = document.getElementById('srpPriceOptions')?.checked ?? true;
                payload.direction      = 'long';
                payload.st_period      = sen('srpStLen', 10);
                payload.st_mult        = sen('srpStFactor', 2.3);
                payload.rsi_period     = sen('srpRsiLen', 14);
                payload.rsi_level      = sen('srpRsiLevel', 70);
                payload.bb_period      = sen('srpBbLen', 15);
                payload.bb_mult        = sen('srpBbMult', 3);
                payload.sl_pct         = sen('srpSlPct', 0);
                payload.tsl_x          = sen('srpTslX', 0);
                payload.tsl_y          = sen('srpTslY', 0);
                payload.entry_start    = sev('srpEntryStart', '09:31');
                payload.entry_end      = sev('srpEntryEnd', '15:14');
                payload.exit_cutoff    = sev('srpExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('srpMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding    = document.getElementById('srpCompound')?.checked ?? false;
                payload.start_capital  = sen('srpCapital', 25000);
                payload.per_lot        = sen('srpPerLot', 0);
            }

            // ST + RSI + PP + BB + green candle: long NIFTY-future signal, bought as the ≈250 CE with a premium SL / TSL
            if (strat === 'st_rsi_pp_bb_fut') {
                endpoint = '/api/backtest/st-rsi-pp-bb-fut';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '5minute');
                payload.premium        = parseFloat(sev('srfPremium', 250));
                payload.price_options  = document.getElementById('srfPriceOptions')?.checked ?? true;
                payload.direction      = 'long';
                payload.require_green  = document.getElementById('srfGreen')?.checked ?? true;
                payload.st_period      = sen('srfStLen', 10);
                payload.st_mult        = sen('srfStFactor', 1.8);
                payload.rsi_period     = sen('srfRsiLen', 20);
                payload.rsi_level      = sen('srfRsiLevel', 68);
                payload.bb_period      = sen('srfBbLen', 20);
                payload.bb_mult        = sen('srfBbMult', 3.3);
                payload.sl_pct         = sen('srfSlPct', 0);
                payload.tsl_x          = sen('srfTslX', 0);
                payload.tsl_y          = sen('srfTslY', 0);
                payload.entry_start    = sev('srfEntryStart', '09:15');
                payload.entry_end      = sev('srfEntryEnd', '15:14');
                payload.exit_cutoff    = sev('srfExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('srfMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding    = document.getElementById('srfCompound')?.checked ?? false;
                payload.start_capital  = sen('srfCapital', 25000);
                payload.per_lot        = sen('srfPerLot', 0);
            }

            // ST + RSI + CPR (Demark) + BB: long SENSEX-index signal, bought as the ≈500 CE with a premium SL / TSL
            if (strat === 'st_rsi_cpr_bb_sx') {
                endpoint = '/api/backtest/st-rsi-cpr-bb-sx';
                payload.symbol = 'SENSEX';
                selectedSymbol = 'SENSEX';
                symbolSearch.value = 'SENSEX';
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '5minute');
                payload.premium        = parseFloat(sev('scxPremium', 500));
                payload.price_options  = document.getElementById('scxPriceOptions')?.checked ?? true;
                payload.direction      = 'long';
                payload.st_period      = sen('scxStLen', 10);
                payload.st_mult        = sen('scxStFactor', 1.5);
                payload.rsi_period     = sen('scxRsiLen', 14);
                payload.rsi_level      = sen('scxRsiLevel', 70);
                payload.bb_period      = sen('scxBbLen', 20);
                payload.bb_mult        = sen('scxBbMult', 2.5);
                payload.cpr_wide       = sen('scxCprWide', 0.35);
                payload.sl_pct         = sen('scxSlPct', 0);
                payload.tsl_x          = sen('scxTslX', 0);
                payload.tsl_y          = sen('scxTslY', 0);
                payload.entry_start    = sev('scxEntryStart', '09:15');
                payload.entry_end      = sev('scxEntryEnd', '15:14');
                payload.exit_cutoff    = sev('scxExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('scxMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 20);
                payload.compounding    = document.getElementById('scxCompound')?.checked ?? false;
                payload.start_capital  = sen('scxCapital', 25000);
                payload.per_lot        = sen('scxPerLot', 0);
            }

            // ST + RSI + WR + CPR (Traditional, narrow) + BB (SX2): long SENSEX-index signal, bought as the ≈500 CE with a premium SL / TSL
            if (strat === 'st_rsi_cpr_bb_wr_sx') {
                endpoint = '/api/backtest/st-rsi-cpr-bb-wr-sx';
                payload.symbol = 'SENSEX';
                selectedSymbol = 'SENSEX';
                symbolSearch.value = 'SENSEX';
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '5minute');
                payload.premium        = parseFloat(sev('swxPremium', 500));
                payload.price_options  = document.getElementById('swxPriceOptions')?.checked ?? true;
                payload.direction      = 'long';
                payload.st_period      = sen('swxStLen', 10);
                payload.st_mult        = sen('swxStFactor', 1.5);
                payload.rsi_period     = sen('swxRsiLen', 14);
                payload.rsi_level      = sen('swxRsiLevel', 68);
                payload.rsi_cap        = sen('swxRsiCap', 80);
                payload.wr_period      = sen('swxWrLen', 14);
                payload.wr_level       = sen('swxWrLevel', -10);
                payload.bb_period      = sen('swxBbLen', 20);
                payload.bb_mult        = sen('swxBbMult', 2.5);
                payload.cpr_narrow     = sen('swxCprNarrow', 0.2);
                payload.sl_pct         = sen('swxSlPct', 0);
                payload.tsl_x          = sen('swxTslX', 0);
                payload.tsl_y          = sen('swxTslY', 0);
                payload.entry_start    = sev('swxEntryStart', '09:15');
                payload.entry_end      = sev('swxEntryEnd', '15:14');
                payload.exit_cutoff    = sev('swxExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('swxMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 20);
                payload.compounding    = document.getElementById('swxCompound')?.checked ?? false;
                payload.start_capital  = sen('swxCapital', 25000);
                payload.per_lot        = sen('swxPerLot', 0);
            }

            // ST + RSI + CCI + WR + PP + BB: long NIFTY-future signal, bought as the ≈250 CE with a premium SL / TSL
            if (strat === 'st_rsi_cci_wr_pp_bb_fut') {
                endpoint = '/api/backtest/st-rsi-cci-wr-pp-bb-fut';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const sev = (id, d) => document.getElementById(id)?.value || d;
                const sen = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = sev('interval', '3minute');
                payload.premium        = parseFloat(sev('scwPremium', 250));
                payload.price_options  = document.getElementById('scwPriceOptions')?.checked ?? true;
                payload.direction      = 'long';
                payload.st_period      = sen('scwStLen', 10);
                payload.st_mult        = sen('scwStFactor', 2);
                payload.rsi_period     = sen('scwRsiLen', 14);
                payload.rsi_level      = sen('scwRsiLevel', 78);
                payload.cci_period     = sen('scwCciLen', 20);
                payload.cci_level      = sen('scwCciLevel', 280);
                payload.wr_period      = sen('scwWrLen', 14);
                payload.wr_level       = sen('scwWrLevel', -15);
                payload.bb_period      = sen('scwBbLen', 20);
                payload.bb_mult        = sen('scwBbMult', 3);
                payload.sl_pct         = sen('scwSlPct', 0);
                payload.tsl_x          = sen('scwTslX', 0);
                payload.tsl_y          = sen('scwTslY', 0);
                payload.entry_start    = sev('scwEntryStart', '09:15');
                payload.entry_end      = sev('scwEntryEnd', '15:14');
                payload.exit_cutoff    = sev('scwExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(sev('scwMaxTrades', 5));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding    = document.getElementById('scwCompound')?.checked ?? false;
                payload.start_capital  = sen('scwCapital', 25000);
                payload.per_lot        = sen('scwPerLot', 0);
            }

            // MA Cross + HTF: index signal, optionally priced on the CE/PE
            if (strat === 'ma_htf') {
                endpoint = '/api/backtest/ma-htf';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const mhv = (id, d) => document.getElementById(id)?.value || d;
                const mhn = (id, d) => { const v = parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : d; };
                payload.interval       = mhv('interval', '3minute');
                payload.premium        = parseFloat(mhv('mhPremium', 250));
                payload.price_options  = document.getElementById('mhPriceOptions')?.checked ?? true;
                payload.positional     = document.getElementById('mhPositional')?.checked ?? false;
                payload.roll           = mhv('mhRoll', 'same');
                payload.min_sessions   = parseInt(mhv('mhMinSessions', 5));
                payload.direction      = mhv('mhDirection', 'both');
                payload.fast_len       = parseInt(mhv('mhFast', 11));
                payload.slow_len       = parseInt(mhv('mhSlow', 21));
                payload.htf_minutes    = parseInt(mhv('mhHtfMin', 15));
                payload.htf_len        = parseInt(mhv('mhHtfLen', 21));
                payload.htf_type       = mhv('mhHtfType', 'sma');
                payload.htf_calc       = mhv('mhHtfCalc', 'live');
                payload.confirm_bars   = mhn('mhConfirm', 1);
                payload.max_wait       = mhn('mhMaxWait', 5);
                payload.sl_points      = mhn('mhSl', 35);
                payload.partial_pct    = mhn('mhPartialPct', 0);
                payload.partial_points = mhn('mhPartialPts', 0);
                payload.exit_on_cross  = document.getElementById('mhExitCross')?.checked ?? true;
                payload.trail_points   = mhn('mhTrail', 0);
                payload.trail_start    = mhn('mhTrailStart', 0);
                payload.adx_min        = mhn('mhAdx', 0);
                payload.chop_lookback  = mhn('mhChopLb', 0);
                payload.chop_max       = mhn('mhChopMax', 2);
                payload.day_bias       = document.getElementById('mhDayBias')?.checked ?? false;
                payload.entry_start    = mhv('mhEntryStart', '09:30');
                payload.entry_end      = mhv('mhEntryEnd', '14:30');
                payload.exit_cutoff    = mhv('mhExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(mhv('mhMaxTrades', 2));
                payload.lots           = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value      = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding    = document.getElementById('mhCompound')?.checked ?? false;
                payload.start_capital  = mhn('mhCapital', 25000);
                payload.per_lot        = mhn('mhPerLot', 0);
            }

            // Hilega Milega: index signal, optionally priced on the CE/PE
            if (strat === 'hilega_milega') {
                endpoint = '/api/backtest/hilega-milega';
                if (!payload.symbol || payload.symbol === ALL_STOCKS) {
                    payload.symbol = 'NIFTY';
                    selectedSymbol = 'NIFTY';
                    symbolSearch.value = 'NIFTY';
                }
                const hmv = (id, d) => document.getElementById(id)?.value || d;
                payload.interval      = hmv('interval', '60minute');
                payload.premium       = parseFloat(hmv('hmPremium', 250));
                payload.price_options = document.getElementById('hmPriceOptions')?.checked ?? true;
                payload.direction     = hmv('hmDirection', 'both');
                payload.rsi_period    = parseInt(hmv('hmRsiLen', 9));
                payload.green_len     = parseInt(hmv('hmGreenLen', 3));
                payload.red_len       = parseInt(hmv('hmRedLen', 21));
                payload.mid           = parseFloat(hmv('hmMid', 50));
                payload.arm_bars      = parseInt(hmv('hmArmBars', 5));
                payload.min_gap       = parseFloat(document.getElementById('hmMinGap')?.value || 0);
                payload.exit_mode     = hmv('hmExitMode', 'green');
                payload.entry_mode    = hmv('hmEntryMode', 'either');
                payload.pb_band       = parseFloat(document.getElementById('hmPbBand')?.value || 10);
                payload.chop_lookback = parseInt(document.getElementById('hmChop')?.value || 12);
                payload.htf           = hmv('hmHtf', 'off');
                payload.rr            = parseFloat(document.getElementById('hmRr')?.value || 0);
                payload.sl_on_close   = document.getElementById('hmSlOnClose')?.checked ?? true;
                payload.entry_start   = hmv('hmEntryStart', '10:15');
                payload.entry_end     = hmv('hmEntryEnd', '15:14');
                payload.exit_cutoff   = hmv('hmExitCutoff', '15:14');
                payload.max_trades_per_day = parseInt(hmv('hmMaxTrades', 1));
                payload.positional    = document.getElementById('hmPositional')?.checked ?? true;
                payload.roll          = hmv('hmRoll', 'same');
                payload.min_sessions  = parseInt(document.getElementById('hmMinSessions')?.value ?? 5);
                payload.lots          = Math.max(1, parseInt(document.getElementById('pcLots')?.value || 1));
                payload.lot_value     = parseFloat(document.getElementById('pcLotValue')?.value || 65);
                payload.compounding   = document.getElementById('hmCompound')?.checked ?? false;
                payload.start_capital = parseFloat(document.getElementById('hmCapital')?.value || 25000);
                payload.per_lot       = parseFloat(document.getElementById('hmPerLot')?.value || 0);
            }

            // 30-Min Opening Fakeout breakdown/breakout
            if (strat === 'thirty_min_fakeout') {
                endpoint = '/api/backtest/thirty-min-fakeout';
                // payload.symbol defaults to the shared (hidden-for-TMF) Symbol
                // search field's stale value — override with the TMF dropdown's
                // choice, or clear it entirely for a full universe scan.
                payload.symbol = document.getElementById('tmfSymbol')?.value || '';
                payload.direction = document.getElementById('tmfDirection')?.value || 'both';
                const exitTime = (document.getElementById('tmfExitTime')?.value || '15:18').split(':');
                payload.exit_hour   = parseInt(exitTime[0] || 15);
                payload.exit_minute = parseInt(exitTime[1] || 18);
                payload.use_entry_buffer   = document.getElementById('tmfUseEntryBuffer')?.checked ?? true;
                payload.use_body_filter    = document.getElementById('tmfUseBodyFilter')?.checked ?? true;
                payload.use_c2_close_filter = document.getElementById('tmfUseC2CloseFilter')?.checked ?? true;
                payload.use_symbol_defaults = document.getElementById('tmfUseSymbolDefaults')?.checked ?? true;
                payload.use_sl_risk_filter = document.getElementById('tmfUseSlRiskFilter')?.checked ?? true;
                payload.sl_risk_max = parseFloat(document.getElementById('tmfSlRiskMax')?.value || '5000') || 5000;
            }

            // EMA Confluence Breakout
            if (strat === 'ema_pullback') {
                endpoint = '/api/backtest/ema-pullback';
                // With "All Stocks" the backend scans every symbol in
                // EMA_SYMBOL_DEFAULTS. use_symbol_defaults on (the default)
                // runs each on its OWN Direction/Target from that table; off
                // applies the two fields below to every stock instead. There
                // is no "symbol missing from the table" case any more — that
                // table IS the universe, and the server refuses anything
                // outside it. For a SINGLE symbol these two fields are its own
                // prefilled row unless the user has deliberately changed them,
                // and omitting them makes the server use that row too. Lots is
                // what sizes each stock (× its own lot size) into ₹.
                payload.direction   = document.getElementById('emaDirection')?.value || 'both';
                payload.target_pct  = Math.max(1, parseFloat(document.getElementById('emaTargetPct')?.value || 5));
                payload.lots        = Math.max(1, parseInt(document.getElementById('emaLots')?.value || 1));
                payload.use_symbol_defaults = document.getElementById('emaUseSymbolDefaults')?.checked ?? true;
                // Applies to every stock — the per-stock table carries
                // Direction/Target only, not this gate.
                payload.require_rr  = document.getElementById('emaRequireRR')?.checked ?? false;
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

            // Find Best Params asks for the payload this strategy's run would post, so the
            // sweep reads exactly the filters the page has set — nothing is sent.
            if (endpoint && window._btCapture) {
                const cb = window._btCapture;
                window._btCapture = null;
                cb({ endpoint, payload });
                return;
            }

            if (!endpoint) {
                window.showNotification('Pick a strategy first', 'warning');
                return;
            }

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            let data = await response.json();

            // Simple Trader / Hilega / MA: signals come back at once, the option legs are
            // priced in the background (Breeze, paced) and polled here.
            if (data.success && data.task_id && (strat === 'simpler_trader' || strat === 'hilega_milega' || strat === 'ma_htf' || strat === 'st_ema_rsi_cci' || strat === 'st_rsi_pp_bb' || strat === 'st_rsi_pp_bb_fut' || strat === 'st_rsi_cci_wr_pp_bb_fut' || strat === 'st_rsi_cpr_bb_sx' || strat === 'st_rsi_cpr_bb_wr_sx')) {
                const taskId = data.task_id;
                const t0 = Date.now();
                while (true) {
                    _showOptLoader('Pricing the ≈' + payload.premium + ' option legs',
                        'starting…');
                    await new Promise(r => setTimeout(r, 2000));
                    const st = await (await fetch(`/api/backtest/st-bb-rsi/status/${taskId}`)).json();
                    if (st.status === 'running') {
                        const p = st.progress || {};
                        _showOptLoader('Pricing the ≈' + payload.premium + ' option legs',
                            `${p.done || 0} / ${p.total || 0} trades · ${st.elapsed || Math.round((Date.now() - t0) / 1000)}s`);
                        continue;
                    }
                    data = st;
                    break;
                }
                _hideOptLoader();
            }

            if (data.success) {
                if (data.warning) window.showNotification(data.warning, 'warning');
                if (data.ran && data.ran.strategy === 'st_rsi_cpr_bb_wr_sx') {
                    const r = data.ran, p = r.params;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · ST ${p.st.join('/')}, RSI ${p.rsi[0]}-${p.rsi[2]}, %R ${p.wr[0]} >${p.wr[1]}, BB ${p.bb.join('/')}, `
                        + `CPR ${p.cpr_narrow ? 'narrow <' + p.cpr_narrow + '%' : 'any'}, SL ${p.sl_pct}% TSL ${p.tsl.join('/')}%`, 'info');
                } else if (data.ran && data.ran.strategy === 'st_rsi_cpr_bb_sx') {
                    const r = data.ran, p = r.params;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · ST ${p.st.join('/')}, RSI ${p.rsi[0]} >${p.rsi[1]}, BB ${p.bb.join('/')}, `
                        + `CPR ${p.cpr_wide ? '<' + p.cpr_wide + '%' : 'any'}, SL ${p.sl_pct}% TSL ${p.tsl.join('/')}%`, 'info');
                } else if (data.ran && (data.ran.strategy === 'st_rsi_pp_bb' || data.ran.strategy === 'st_rsi_pp_bb_fut')) {
                    const r = data.ran, p = r.params;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · ST ${p.st.join('/')}, RSI ${p.rsi[0]} >${p.rsi[1]}, BB ${p.bb.join('/')}, `
                        + `SL ${p.sl_pct}% TSL ${p.tsl.join('/')}%`, 'info');
                } else if (data.ran && data.ran.strategy === 'st_rsi_cci_wr_pp_bb_fut') {
                    const r = data.ran, p = r.params;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · ST ${p.st.join('/')}, RSI ${p.rsi[0]} >${p.rsi[1]}, CCI ${p.cci[0]} >${p.cci[1]}, `
                        + `%R ${p.wr[0]} >${p.wr[1]}, BB ${p.bb.join('/')}, SL ${p.sl_pct}% TSL ${p.tsl.join('/')}%`, 'info');
                } else if (data.ran && data.ran.strategy === 'st_ema_rsi_cci') {
                    const r = data.ran, p = r.params;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · ST ${p.st.join('/')}, EMA ${p.ema}, RSI ${p.rsi[0]} <${p.rsi[1]} exit >${p.rsi[2]}, `
                        + `CCI ${p.cci[0]} <${p.cci[1]}, %R ${p.wr[0]} <${p.wr[1]}, BB ${p.bb.join('/')}, `
                        + `CPR ${p.use_cpr_narrow ? '<' + p.cpr_narrow + '%' : 'any'}, SL ${p.sl_pct}% TSL ${p.tsl.join('/')}%`, 'info');
                } else if (data.ran && data.ran.strategy === 'ma_htf') {
                    const r = data.ran;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · SMMA ${r.params.fast_len}/${r.params.slow_len}, `
                        + `HTF ${r.params.htf_type.toUpperCase()} ${r.params.htf_len} on ${r.params.htf_minutes}m, `
                        + `stop ${r.params.sl_points}`, 'info');
                } else if (data.ran && data.ran.strategy === 'hilega_milega') {
                    const r = data.ran;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars · `
                        + `${r.index_signals} signals · RSI ${r.params.rsi_period}, green ${r.params.green_len}, red ${r.params.red_len}, `
                        + `entry ${r.params.entry_mode}, armed ${r.params.arm_bars}, exit: ${r.params.exit_mode}`, 'info');
                } else if (data.ran) {
                    const r = data.ran;
                    window.showNotification(
                        `${r.symbol} ${r.interval} (${r.data_source}) · ${r.start_date} → ${r.end_date} · ${r.bars} bars `
                        + `(${r.first_bar.slice(0, 10)} … ${r.last_bar.slice(0, 10)}) · `
                        + `${r.index_signals} signals · RSI ${r.params.rsi_period} >${r.params.rsi_level} / <${r.params.rsi_lower}, `
                        + `ST ${r.params.st.join('/')}, BB ${r.params.bb.join('/')}`, 'info');
                }
                // Otherwise the gate is invisible: the run just shows fewer
                // trades with nothing saying why.
                const rrSkipped = data.summary?.rr_skipped || 0;
                if (payload.require_rr && rrSkipped) {
                    window.showNotification(
                        `R:R filter skipped ${rrSkipped} breakout${rrSkipped === 1 ? '' : 's'} worse than 1:1`,
                        'info');
                }
                displayResults(data);
            } else {
                window.showNotification(data.error || 'Backtest failed', 'error');
            }
        } catch (error) {
            console.error('Backtest error:', error);
            window.showNotification('An error occurred during backtest', 'error');
        } finally {
            loading.style.display = 'none';
            // If the run produced no results (error/failure), restore the idle prompt.
            if (resultsArea.style.display !== 'block') {
                const ph = document.getElementById('btRightPlaceholder');
                if (ph) ph.style.display = '';
            }
        }
    });

    // The 30-Sec Option Breakout run and sweep share one progress line. The
    // wait is Breeze, paced at 1.5 requests/s for the whole app, so the line
    // says what has actually been asked for and how long the rest should take
    // at the pace so far — a cold year is minutes, a cached one seconds, and
    // without the estimate both looked hung.
    function _obProgressLine(data) {
        const elapsed = data.elapsed || 0;
        const total = data.legs_total || 0;
        const done  = data.legs_done  || 0;
        const reqs  = data.requests   || 0;
        let line = `${elapsed}s elapsed`;
        if (!total) return line + ' · starting…';
        line += ` · ${done}/${total} contracts`;
        if (done >= 3 && done < total) {
            const left = Math.round(elapsed / done * (total - done));
            line += ` · ≈ ${left >= 90 ? Math.round(left / 60) + ' min' : left + 's'} left`;
        }
        if (reqs) line += ` · ${reqs} Breeze req${elapsed ? ' @ ' + (reqs / elapsed).toFixed(1) + '/s' : ''}`;
        if (data.stage) line += ` · ${data.stage}`;
        return line;
    }

    // ── NIFTY Option Selling — start + poll ────────────────────────────
    // Two Breeze requests per trading day (one per leg), disk-cached once the
    // day has settled: a year is a few minutes cold, instant warm. Own result
    // panel — a row is one day's strangle, not a trade the shared cards fit.
    let _osRun = 0;
    async function runOptionSelling() {
        const days = Array.from(document.querySelectorAll('.osDay:checked')).map(c => parseInt(c.value));
        const payload = {
            start_date:  document.getElementById('startDate')?.value,
            end_date:    document.getElementById('endDate')?.value,
            days,
            entry_time:  document.getElementById('osEntryTime')?.value || '09:30',
            exit_time:   document.getElementById('osExitTime')?.value || '10:30',
            otm_strikes: parseInt(document.getElementById('osOtm')?.value ?? 2),
            sl_pct:      parseFloat(document.getElementById('osSlPct')?.value || 60),
            reentry:     document.getElementById('osReentry')?.value !== 'false',
            lots:        Math.max(1, parseInt(document.getElementById('osLots')?.value || 1)),
        };
        if (!days.length) { window.showNotification('Pick at least one trading day', 'warning'); return; }
        const myRun = ++_osRun;
        resultsArea.style.display = 'none';
        const osRes = document.getElementById('osResults');
        if (osRes) osRes.style.display = 'none';
        const btTradesSec = document.getElementById('btTradesSection');
        const periodSec   = document.getElementById('periodBreakdownSection');
        if (btTradesSec) btTradesSec.style.display = 'none';
        if (periodSec)   periodSec.style.display   = 'none';
        _showOptLoader('Running NIFTY Option Selling', 'Starting…');
        try {
            const resp = await fetch('/api/backtest/option-selling', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Backtest failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2000));
                if (myRun !== _osRun) return;
                data = await (await fetch(`/api/backtest/option-selling/status/${taskId}`)).json();
                if (myRun !== _osRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Running NIFTY Option Selling',
                    `${p.done || 0} / ${p.total || 0} days · ${p.traded || 0} priced (ICICI) · ${data.elapsed || 0}s`);
            }
            _hideOptLoader();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Backtest failed');
            renderOptionSelling(data);
        } catch (err) {
            if (myRun !== _osRun) return;
            _hideOptLoader();
            console.error('Option Selling error:', err);
            window.showNotification(err.message || 'Backtest failed', 'error');
            const ph = document.getElementById('btRightPlaceholder');
            if (ph) ph.style.display = '';
        }
    }

    function renderOptionSelling(data) {
        const s = data.summary || {};
        const inr = v => (v >= 0 ? '+' : '−') + '₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');
        const card = (lbl, val, cls, sub) => `<div class="stat-card ${cls || ''}">
            <span class="stat-card__lbl">${lbl}</span>
            <span class="stat-card__val ${val.cls || ''}">${val.text ?? val}</span>
            ${sub ? `<small>${sub}</small>` : ''}</div>`;
        const tone = v => ({ text: inr(v), cls: v >= 0 ? 'stat-val-green' : 'stat-val-red' });
        const stats = document.getElementById('osStats');
        if (stats) stats.innerHTML = [
            card('Days Traded', String(s.total_trades || 0), 'ac-blue',
                 `${s.skipped_days || 0} skipped (no premiums) of ${s.days_scanned || 0}`),
            card('Win Rate', `${(s.win_rate || 0).toFixed(1)}%`, 'ac-green', `${s.wins || 0} W / ${s.losses || 0} L`),
            card('Net P&L', tone(s.total_pnl_rupees || 0), 'ac-blue',
                 `gross ${inr(s.total_gross_pnl_rupees || 0)} · brok ₹${Math.round(s.total_brokerage || 0).toLocaleString('en-IN')}`),
            card('Profit Factor', String(s.profit_factor ?? 0), 'ac-blue',
                 `avg win ${inr(s.avg_win_rupees || 0)} · avg loss −₹${Math.round(s.avg_loss_rupees || 0).toLocaleString('en-IN')}`),
            card('Max Drawdown', { text: '−₹' + Math.round(s.max_drawdown_rupees || 0).toLocaleString('en-IN'), cls: 'stat-val-red' }, 'ac-red'),
            card('Stops / Re-entries', `${s.sl_hits || 0} / ${s.reentries || 0}`, 'ac-blue',
                 `${s.otm_strikes}-OTM · SL ${s.sl_pct}% · ${s.lots}×${s.lot_size}`),
        ].join('');
        const prem = v => (v == null ? '—' : Number(v).toFixed(2));
        const leg = (k, label) => [
            { key: `${k}_strike`, label: `${label} Strike`, sortable: true, format: v => v ?? '—' },
            { key: `${k}_entry`, label: `${label} Sold`, sortable: true, format: prem },
            { key: `${k}_exit`, label: `${label} Exit`, sortable: true, format: prem },
            { key: `${k}_reentry`, label: `${label} Re-sold`, sortable: true, format: prem },
            { key: `${k}_pnl`, label: `${label} Pts`, sortable: true, format: prem, tone: DataGrid.sign },
        ];
        DataGrid.mountSortable('osGrid', {
            rows: data.trades || [],
            columns: [
                { key: 'date', label: 'Date', sortable: true },
                { key: 'weekday', label: 'Day', sortable: true },
                { key: 'spot', label: 'Spot', sortable: true, format: v => (v || 0).toFixed(2) },
                ...leg('ce', 'CE'), ...leg('pe', 'PE'),
                { key: 'exit_reason', label: 'Outcome', sortable: true },
                { key: 'pnl', label: 'Points', sortable: true, format: v => (v || 0).toFixed(2), tone: DataGrid.sign },
                { key: 'pnl_rupees', label: 'Net P&L', sortable: true, format: DataGrid.inr, tone: DataGrid.sign },
            ],
            empty: s.last_breeze_error
                ? `No days could be priced — ICICI Breeze said: ${s.last_breeze_error}. Its history service may be down; retry later.`
                : 'No days could be priced — no option candles for the strikes in this range',
            defaultSort: { key: 'date', dir: 'desc' },
        });
        const osRes = document.getElementById('osResults');
        if (osRes) osRes.style.display = '';
        if (s.breeze_errors) window.showNotification(
            `${s.breeze_errors} ICICI request${s.breeze_errors === 1 ? '' : 's'} failed (${s.last_breeze_error}) — those days are missing, not zero`, 'warning');
        if (s.skipped_days) window.showNotification(
            `${s.skipped_days} day${s.skipped_days === 1 ? '' : 's'} left out — no ICICI option candles`, 'info');
    }

    // ── Weekly CPR Breakout — start + poll ─────────────────────────────
    // Two Fyers daily requests per stock plus whatever 1-minute tail is not on
    // disk yet: a minute or two warm, several cold. The server answers with a
    // task id; this polls it and shows how many stocks are done.
    let _wcbRun = 0;
    async function runWeeklyCprBreakout() {
        const payload = {
            // ALL STOCKS -> '' (every F&O future stock); anything else is
            // that one stock.
            symbol:     selectedSymbol === ALL_STOCKS ? '' : (selectedSymbol || '').toUpperCase(),
            start_date: document.getElementById('startDate')?.value,
            end_date:   document.getElementById('endDate')?.value,
            direction:  document.getElementById('wcbDirection')?.value || 'both',
            rr_ratio:   parseFloat(document.getElementById('wcbRrRatio')?.value || 1.5),
            exit_time:  document.getElementById('wcbExitTime')?.value || '15:20',
            max_cpr_ratio: document.getElementById('wcbMaxRatio')?.value || '0.5',
            basis:      document.getElementById('wcbBasis')?.value || 'option',
            signal:     document.getElementById('wcbSignal')?.value || 'candle',
            candle_type: document.getElementById('wcbCandleType')?.value || 'all',
            // 0 is meaningful — it turns the range filter off.
            min_range_pct: parseFloat(document.getElementById('wcbMinRange')?.value ?? 1.6),
            lots:       Math.max(1, parseInt(document.getElementById('wcbLots')?.value || 1)),
        };
        const myRun = ++_wcbRun;
        resultsArea.style.display = 'none';
        const btTradesSec = document.getElementById('btTradesSection');
        const periodSec   = document.getElementById('periodBreakdownSection');
        if (btTradesSec) btTradesSec.style.display = 'none';
        if (periodSec)   periodSec.style.display   = 'none';
        _showOptLoader('Running Weekly CPR Breakout', 'Starting…');
        try {
            const resp = await fetch('/api/backtest/weekly-cpr-breakout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Backtest failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2000));
                if (myRun !== _wcbRun) return;             // superseded
                data = await (await fetch(`/api/backtest/weekly-cpr-breakout/status/${taskId}`)).json();
                if (myRun !== _wcbRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Running Weekly CPR Breakout',
                    `${p.done || 0} / ${p.total || 0} stocks`
                    + (payload.basis === 'option' ? ` · ${p.priced || 0} option legs priced (ICICI)` : '')
                    + ` · ${data.elapsed || 0}s`);
            }
            _hideOptLoader();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Backtest failed');
            const s = data.summary || {};
            if (s.unpriced) window.showNotification(
                `${s.unpriced} trade${s.unpriced === 1 ? '' : 's'} left out — no ICICI option candles for the strike`, 'info');
            if (s.symbols_no_data) window.showNotification(
                `${s.symbols_no_data} stock${s.symbols_no_data === 1 ? '' : 's'} had no Fyers data for this range`, 'info');
            displayResults(data);
        } catch (err) {
            if (myRun !== _wcbRun) return;
            _hideOptLoader();
            console.error('Weekly CPR Breakout error:', err);
            window.showNotification(err.message || 'Backtest failed', 'error');
            const ph = document.getElementById('btRightPlaceholder');
            if (ph) ph.style.display = '';
        }
    }

    // ── Heikin-Ashi + SuperTrend — start + poll ─────────────────────────
    // Daily bars for every F&O stock, mostly off the per-stock disk store.
    let _hastRun = 0;
    async function runHaSupertrend() {
        const v = (id, d) => document.getElementById(id)?.value ?? d;
        const payload = {
            symbol:     selectedSymbol === ALL_STOCKS ? '' : (selectedSymbol || '').toUpperCase(),
            start_date: v('startDate'), end_date: v('endDate'),
            interval:   'day',
            direction:  v('hastDirection', 'both'),
            st_period:  parseInt(v('hastPeriod', 10)), st_mult: parseFloat(v('hastMult', 3)),
            st_source:  v('hastSource', 'ha'),
            swing_len:  parseInt(v('hastSwing', 8)), fresh_bars: parseInt(v('hastFresh', 2)),
            rr:         parseFloat(v('hastRr', 2)), partial: parseFloat(v('hastPartial', 50)) / 100,
            opp_candles: parseInt(v('hastOpp', 2)), max_risk_pct: parseFloat(v('hastMaxRisk', 25)),
            be_after_t1: v('hastBe', 'no') === 'yes',
            stop_on_close: v('hastStopClose', 'yes') === 'yes',
            capital_per_trade: parseFloat(v('hastCapital', 25000)), cost_pct: parseFloat(v('hastCost', 0.2)),
        };
        const myRun = ++_hastRun;
        resultsArea.style.display = 'none';
        const btTradesSec = document.getElementById('btTradesSection');
        const periodSec   = document.getElementById('periodBreakdownSection');
        if (btTradesSec) btTradesSec.style.display = 'none';
        if (periodSec)   periodSec.style.display   = 'none';
        _showOptLoader('Running Heikin-Ashi + SuperTrend', 'Starting…');
        try {
            const resp = await fetch('/api/backtest/ha-supertrend', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Backtest failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2000));
                if (myRun !== _hastRun) return;            // superseded
                data = await (await fetch(`/api/backtest/ha-supertrend/status/${taskId}`)).json();
                if (myRun !== _hastRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Running Heikin-Ashi + SuperTrend',
                    `${p.done || 0} / ${p.total || 0} stocks · ${data.elapsed || 0}s`);
            }
            _hideOptLoader();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Backtest failed');
            const s = data.summary || {};
            if (s.symbols_no_data) window.showNotification(
                `${s.symbols_no_data} stock${s.symbols_no_data === 1 ? '' : 's'} had no Fyers data for this range`, 'info');
            displayResults(data);
        } catch (err) {
            if (myRun !== _hastRun) return;
            _hideOptLoader();
            console.error('HA + SuperTrend error:', err);
            window.showNotification(err.message || 'Backtest failed', 'error');
            const ph = document.getElementById('btRightPlaceholder');
            if (ph) ph.style.display = '';
        }
    }

    // ── 30-Sec Option Breakout — start + poll ───────────────────────────
    // The server fetches one contract's premiums per Breeze request at 1.5
    // req/s, so a run is minutes, not seconds. It answers with a task id and
    // this walks it: every poll says which contract just landed, because a
    // silent three-minute spinner is indistinguishable from a hung request.

    function cancelObRun() {
        _obRun += 1;
        if (_obAbort) {
            try { _obAbort.abort(); } catch (e) { /* noop */ }
            _obAbort = null;
        }
        _hideOptLoader();
    }

    async function runOptionBreakout() {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const payload = {
            symbol:       symbol,
            start_date:   document.getElementById('startDate')?.value,
            end_date:     document.getElementById('endDate')?.value,
            candle_index: parseInt(document.getElementById('obCandleIndex')?.value || 2),
            rr_ratio:     parseFloat(document.getElementById('obRrRatio')?.value || 2),
            // 0 is meaningful — it puts the stop exactly on the range low.
            sl_buffer:    parseFloat(document.getElementById('obSlBuffer')?.value ?? 1),
            legs:         document.getElementById('obLegs')?.value || 'both',
        };
        const obExit = (document.getElementById('obExitTime')?.value || '15:25').split(':');
        payload.exit_hour   = parseInt(obExit[0] || 15);
        payload.exit_minute = parseInt(obExit[1] || 25);

        const myRun = ++_obRun;
        resultsArea.style.display = 'none';
        const btTradesSec = document.getElementById('btTradesSection');
        const periodSec   = document.getElementById('periodBreakdownSection');
        if (btTradesSec) btTradesSec.style.display = 'none';
        if (periodSec)   periodSec.style.display   = 'none';
        _showOptLoader('Running option backtest', 'Naming the contracts…');

        try {
            _obAbort = new AbortController();
            const resp = await fetch('/api/backtest/option-breakout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal: _obAbort.signal,
            });
            const data = await resp.json();
            if (myRun !== _obRun) return;
            if (!data.success || !data.task_id) {
                _hideOptLoader();
                window.showNotification(data.error || 'Option backtest failed', 'error');
                return;
            }
            _pollOptionBreakout(data.task_id, Date.now(), myRun);
        } catch (err) {
            if (myRun !== _obRun) return;      // cancelled — not a failure
            console.error('Option breakout error:', err);
            _hideOptLoader();
            window.showNotification('Option backtest request failed', 'error');
        }
    }

    function _pollOptionBreakout(taskId, startMs, myRun) {
        // 30 sessions × 2 legs at ~33s a session is the worst case the Days
        // field allows; the cap sits well past it so a slow-but-alive run is
        // never mistaken for a dead one.
        const MAX_WAIT_MS = 45 * 60 * 1000;

        function tick() {
            if (myRun !== _obRun) return;      // cancelled / superseded
            if (Date.now() - startMs > MAX_WAIT_MS) {
                _hideOptLoader();
                window.showNotification(
                    'The option backtest is taking unusually long — it keeps running on '
                    + 'the server, and the sessions it already fetched are cached, so '
                    + 'running it again will be much faster.', 'warning');
                return;
            }

            fetch(`/api/backtest/option-breakout/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _obRun) return;
                    if (data.status === 'running') {
                        _showOptLoader('Running option backtest', _obProgressLine(data));
                        setTimeout(tick, 2000);
                        return;
                    }
                    _obAbort = null;
                    _hideOptLoader();
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Option backtest failed', 'error');
                        const ph = document.getElementById('btRightPlaceholder');
                        if (ph) ph.style.display = '';
                        return;
                    }
                    if (data.warning) window.showNotification(data.warning, 'warning');
                    displayResults(data);
                })
                .catch(err => {
                    if (myRun !== _obRun) return;
                    console.error('Option breakout poll error:', err);
                    setTimeout(tick, 3000);    // transient network error — retry
                });
        }

        setTimeout(tick, 1500);
    }

    // ── 30-Sec Option Breakout — Find Best Params ───────────────────────
    // Sweeps range candle # × SL:Target × SL buffer × legs on the one timeframe
    // this strategy has. The sessions are the same ones a plain run walks and
    // the server fetches them once, so the sweep costs what a run costs — the
    // premiums are the expensive part, the 144-combo grid is not.
    function _obOptMoney() {
        const lots     = Math.max(1, parseInt(document.getElementById('obLots')?.value     || 1));
        const lotValue = Math.max(1, parseFloat(document.getElementById('obLotValue')?.value || 65));
        return { lots, lotValue };
    }
    function _obOptBrokerage(r, lots) { return calcBrokeragePerTrade(lots) * (r.total_trades || 0); }
    function _obOptNetRs(r, lots, lotValue) { return (r.total_pnl || 0) * lotValue * lots - _obOptBrokerage(r, lots); }

    const OB_OPT_COLS = [
        { label: '#',             key: null,            fmt: (r, i) => i + 1 },
        { label: 'Candle',        key: 'candle_index',  fmt: r => r.candle_index },
        { label: 'Legs',          key: 'legs',          fmt: r => `<span style="white-space:nowrap">${r.legs}</span>` },
        { label: 'SL:Target',     key: 'rr_ratio',      fmt: r => `1:${r.rr_ratio}` },
        // The parameter no other strategy here has: how far below the range
        // low the stop sits, in premium rupees.
        { label: 'Buffer (₹)',    key: 'sl_buffer',     fmt: r => (r.sl_buffer ?? 0).toFixed(1) },
        { label: 'Trades',        key: 'total_trades',  fmt: r => r.total_trades },
        { label: 'Win%',          key: 'win_rate',      fmt: r => `${r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0'}%` },
        { label: 'Net P&L (pts)', key: 'total_pnl',     fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1)} pts</span>` },
        { label: 'Net P&L (₹)',   key: 'net_pnl_inr',   fmt: r => { const { lots, lotValue } = _obOptMoney(); const v = _obOptNetRs(r, lots, lotValue); return `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN')}</span>`; } },
        { label: 'Brokerage (₹)', key: 'brokerage_inr', fmt: r => { const { lots } = _obOptMoney(); const b = _obOptBrokerage(r, lots); return `<span class="pnl-negative">-₹${Math.round(b).toLocaleString('en-IN')}</span>`; } },
        { label: 'Prof. Factor',  key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD',        key: 'max_drawdown',  fmt: r => `<span class="pnl-negative">${r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—'}</span>` },
        { label: '',              key: null,            fmt: () => '' },   // Use button
    ];

    function applyObOptResult(r) {
        const candle   = document.getElementById('obCandleIndex');
        const rr       = document.getElementById('obRrRatio');
        const buffer   = document.getElementById('obSlBuffer');
        const legs     = document.getElementById('obLegs');
        if (candle) candle.value = r.candle_index;
        if (rr)     rr.value     = r.rr_ratio;
        if (buffer) buffer.value = r.sl_buffer;
        if (legs) legs.value = r.legs === 'CE only' ? 'ce' : (r.legs === 'PE only' ? 'pe' : 'both');
        if (window.showNotification) {
            window.showNotification(
                `Applied: candle ${r.candle_index} · 1:${r.rr_ratio}`
                + ` · buffer ₹${r.sl_buffer} · ${r.legs}`
                + `  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success');
        }
    }

    function renderObOptResults(data) {
        const panel     = document.getElementById('optionBreakoutOptimisePanel');
        const metaEl    = document.getElementById('obOptMeta');
        const recalcBtn = document.getElementById('obRecalcOptBtn');

        if (metaEl) {
            // The session window is the headline here in a way it is not on the
            // other boards: this one describes a handful of named days, so a
            // combo's rank means nothing without knowing how few.
            let meta = `${data.combos_kept ?? 0} of ${data.total_combos_tested} combos`
                     + ` · ${data.symbol} · 30 second`;
            if (data.days_scanned) meta += ` · ${data.days_scanned} sessions`;
            if (data.sessions)     meta += ` (${data.sessions})`;
            meta += ' · option premium basis';
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _mountSingleOptGrid('obOptGrid', data.results || [], OB_OPT_COLS,
            'total_pnl', applyObOptResult, {
                win_rate: r => r.total_trades ? r.wins / r.total_trades : 0,
                net_pnl_inr: r => { const { lots, lotValue } = _obOptMoney(); return _obOptNetRs(r, lots, lotValue); },
                brokerage_inr: r => { const { lots } = _obOptMoney(); return _obOptBrokerage(r, lots); },
            });

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyObOptResult(data.best);
    }

    function cancelObOptimise() {
        _obOptRun += 1;
        if (_obOptAbort) {
            try { _obOptAbort.abort(); } catch (e) { /* noop */ }
            _obOptAbort = null;
        }
        _hideOptLoader();
    }

    async function runObOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('optionBreakoutOptimisePanel');
        const recalcBtn = document.getElementById('obRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';
        _showOptLoader('Finding best params…', 'Naming the contracts…');

        const myRun = ++_obOptRun;
        const obExit = (document.getElementById('obExitTime')?.value || '15:25').split(':');
        try {
            _obOptAbort = new AbortController();
            const resp = await fetch('/api/backtest/option-breakout/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate')?.value,
                    end_date:    document.getElementById('endDate')?.value,
                    legs:        document.getElementById('obLegs')?.value || 'both',
                    exit_hour:   parseInt(obExit[0] || 15),
                    exit_minute: parseInt(obExit[1] || 25),
                    recalculate,
                }),
                signal: _obOptAbort.signal,
            });
            const data = await resp.json();
            if (myRun !== _obOptRun) return;
            // A cached board comes back finished, with no task to poll.
            if (data.success && data.status === 'complete') {
                _obOptFinish(data, activeBtn, origText);
                return;
            }
            if (!data.success || !data.task_id) {
                _obOptFinish(null, activeBtn, origText);
                window.showNotification(data.error || 'Optimisation failed', 'error');
                return;
            }
            _pollObOptimise(data.task_id, Date.now(), myRun, activeBtn, origText);
        } catch (err) {
            if (myRun !== _obOptRun) return;      // cancelled — not a failure
            console.error('Option breakout optimise error:', err);
            _obOptFinish(null, activeBtn, origText);
            window.showNotification('Optimisation request failed', 'error');
        }
    }

    function _obOptFinish(data, activeBtn, origText) {
        _obOptAbort = null;
        if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
        _hideOptLoader();
        if (!data) return;
        // A board built on fewer sessions than were asked for is not a shorter
        // answer, it is a different one — say so instead of letting the meta
        // line's session count pass unnoticed.
        if (data.warning) window.showNotification(data.warning, 'warning');
        renderObOptResults(data);
    }

    function _pollObOptimise(taskId, startMs, myRun, activeBtn, origText) {
        // A warm sweep is seconds and a cold one over a long range is minutes;
        // the ceiling sits well clear of the worst case either way.
        const MAX_WAIT_MS = 45 * 60 * 1000;

        function tick() {
            if (myRun !== _obOptRun) return;      // cancelled / superseded
            if (Date.now() - startMs > MAX_WAIT_MS) {
                _obOptFinish(null, activeBtn, origText);
                window.showNotification(
                    'The sweep is taking unusually long — it keeps running on the server '
                    + 'and caches its result, so running it again will load the finished '
                    + 'board.', 'warning');
                return;
            }

            fetch(`/api/backtest/option-breakout/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _obOptRun) return;
                    if (data.status === 'running') {
                        _showOptLoader('Finding best params…', _obProgressLine(data));
                        setTimeout(tick, 2000);
                        return;
                    }
                    if (!data.success || data.status === 'error') {
                        _obOptFinish(null, activeBtn, origText);
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    _obOptFinish(data, activeBtn, origText);
                })
                .catch(err => {
                    if (myRun !== _obOptRun) return;
                    console.error('Option breakout optimise poll error:', err);
                    setTimeout(tick, 3000);       // transient network error — retry
                });
        }

        setTimeout(tick, 1500);
    }

    const obRecalcBtn = document.getElementById('obRecalcOptBtn');
    if (obRecalcBtn) obRecalcBtn.addEventListener('click', () => runObOptimise(true));

    // ── Monthly Expiry Breakout — FILTER mode ───────────────────────────
    // No symbol/dates/SL/Target: scans every F&O stock's 1-hour candles
    // from Jan 1 (this year) to today for a touch-then-close-beyond the
    // stock's current monthly-expiry High/Low — same rule as
    // filters/expiry_hl_scanner.py, applied across the whole range
    // instead of just the latest candle.
    async function runExpiryScan() {
        const expiryResultsArea = document.getElementById('expiryScanResultsArea');
        const btPlaceholder     = document.getElementById('btRightPlaceholder');

        loading.style.display = 'flex';
        resultsArea.style.display = 'none';
        if (expiryResultsArea) expiryResultsArea.style.display = 'none';
        if (btPlaceholder)     btPlaceholder.style.display     = 'none';

        const payload = {
            start_date: document.getElementById('startDate')?.value,
            end_date:   document.getElementById('endDate')?.value,
            timeframe:  document.getElementById('expiryTimeframe')?.value || '60minute',
            ema_touch:  document.getElementById('expiryEmaTouch')?.value || 'touch',
        };

        try {
            const response = await fetch('/api/backtest/expiry-breakout/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json();

            if (data.success) {
                renderExpiryScanResults(data);
            } else {
                window.showNotification(data.error || 'Scan failed', 'error');
            }
        } catch (error) {
            console.error('Expiry breakout scan error:', error);
            window.showNotification('An error occurred during the scan', 'error');
        } finally {
            loading.style.display = 'none';
            if (!expiryResultsArea || expiryResultsArea.style.display !== 'block') {
                if (btPlaceholder) btPlaceholder.style.display = '';
            }
        }
    }

    function renderExpiryScanResults(data) {
        const buy  = data.buy  || [];
        const sell = data.sell || [];
        const all  = [...buy, ...sell];

        // One grid of every signal, grouped by candle timestamp (date + time,
        // latest first) so all the stocks that broke out on the same candle sit
        // together, with BUY and SELL split inside each group. On the 'day'
        // timeframe the candle time is midnight, so the header shows the date
        // alone. Group headers are click-to-collapse.
        function renderRows(rows) {
            if (rows.length === 0) {
                return '<tr><td colspan="5" style="text-align:center;padding:16px;">No signals in this range</td></tr>';
            }
            const groups = new Map();
            for (const r of rows) {
                const key = (r.time || '').replace('T', ' ').slice(0, 16);
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(r);
            }
            const sortedGroups = [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));

            const signalRow = (r, groupId) => `
                <tr data-group-body="${groupId}">
                    <td><a href="https://in.tradingview.com/chart/?symbol=NSE:${r.symbol}" target="_blank" rel="noopener noreferrer" class="expiry-symbol-link">${r.symbol}</a></td>
                    <td>${r.expiry_date || ''}</td>
                    <td>${Number(r.price).toFixed(2)}</td>
                    <td class="pnl-positive">${Number(r.expiry_high).toFixed(2)}</td>
                    <td class="pnl-negative">${Number(r.expiry_low).toFixed(2)}</td>
                </tr>`;

            const sideBlock = (sideRows, groupId, label) => {
                if (!sideRows.length) return '';
                const subHeader = `
                    <tr data-group-body="${groupId}" class="expiry-side-hdr">
                        <td colspan="5">
                            ${label} <span class="expiry-group-count">(${sideRows.length})</span>
                        </td>
                    </tr>`;
                return subHeader + sideRows.map(r => signalRow(r, groupId)).join('');
            };

            return sortedGroups.map(([candleTime, groupRows], gi) => {
                const groupId = `eg-${gi}`;
                const label = candleTime.endsWith(' 00:00') ? candleTime.slice(0, 10) : candleTime;
                const bySymbol = (a, b) => a.symbol.localeCompare(b.symbol);
                const groupBuy  = groupRows.filter(r => r.direction === 'BUY').sort(bySymbol);
                const groupSell = groupRows.filter(r => r.direction === 'SELL').sort(bySymbol);
                const header = `
                    <tr class="expiry-group-hdr" data-group="${groupId}" onclick="window.toggleExpiryGroup(this)">
                        <td colspan="5">
                            <span class="expiry-group-chev">▾</span>
                            ${label}
                            <span class="expiry-group-count">(${groupRows.length})</span>
                        </td>
                    </tr>`;
                return header
                     + sideBlock(groupBuy,  groupId, '🟢 BUY')
                     + sideBlock(groupSell, groupId, '🔴 SELL');
            }).join('');
        }

        const body  = document.getElementById('expiryScanBody');
        const count = document.getElementById('expiryScanCount');
        if (body)  body.innerHTML   = renderRows(all);
        if (count) count.textContent = `(${all.length} — ${buy.length} BUY, ${sell.length} SELL)`;

        const expiryResultsArea = document.getElementById('expiryScanResultsArea');
        if (expiryResultsArea) expiryResultsArea.style.display = 'block';
    }

    // Collapse/expand one candle-timestamp group in the scan results grid.
    window.toggleExpiryGroup = function(headerRow) {
        const groupId = headerRow.dataset.group;
        const rows = headerRow.parentElement.querySelectorAll(`tr[data-group-body="${groupId}"]`);
        if (!rows.length) return;
        const willHide = rows[0].style.display !== 'none';
        rows.forEach(r => { r.style.display = willHide ? 'none' : ''; });
        const chev = headerRow.querySelector('.expiry-group-chev');
        if (chev) chev.textContent = willHide ? '▸' : '▾';
    };

    // Brokerage per round-trip trade (entry + exit) for NIFTY, by lot count
    function calcBrokeragePerTrade(lots) {
        const lookup = { 1: 103, 2: 158, 3: 213, 4: 268, 5: 330 };
        if (lots <= 5) return lookup[Math.max(1, Math.floor(lots))] || 103;
        return 330 + (Math.floor(lots) - 5) * 62;
    }

    // EMA Confluence Breakout trades monthly FUTURES, so the NIFTY options
    // table above does not apply to it: flat ₹1,000 per ORDER, independent of
    // lot count. An entry and an exit are two orders, and every carry-forward
    // roll adds two more (book the near contract, open the far one) — which is
    // why the server sends back summary.total_brokerage off the real order
    // count rather than letting this multiply by trades. Mirrors
    // _EMA_BROKERAGE_PER_ORDER in app/routes/api.py; the fallback below only
    // runs against a response from before futures pricing shipped.
    const EMA_BROKERAGE_PER_ORDER = 1000;

    // Is the Candle Breakout run booked on the option premium? The ₹/pt box
    // applies to either basis, so this only labels the figures and asks the
    // server for the matching leaderboard.
    function _scOptionBasis() {
        return document.getElementById('scOptionPnl')?.checked ?? false;
    }

    let lastData = null;

    function displayResults(data) {
        _displayResultsCore(data);
        applySimplerTraderStats(data);
    }

    // Simple Trader's extra cards, in the Quantman run's own terms: ₹ = premium points × lots × lot
    // size (gross, before brokerage), a streak is unbroken wins/losses (a flat trade breaks both),
    // and Drawdown Days counts the trading days from the equity peak to the trough of the deepest drawdown.
    function applySimplerTraderStats(data) {
        const row  = document.getElementById('simplerStatsRow');
        const isSt = strategySelect && (strategySelect.value === 'simpler_trader' || strategySelect.value === 'st_ema_rsi_cci' || strategySelect.value === 'st_rsi_pp_bb' || strategySelect.value === 'st_rsi_pp_bb_fut' || strategySelect.value === 'st_rsi_cci_wr_pp_bb_fut' || strategySelect.value === 'st_rsi_cpr_bb_sx' || strategySelect.value === 'st_rsi_cpr_bb_wr_sx');
        const setSub = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
        if (row) row.style.display = 'none';
        // Simple Trader's first row: Wins + Losses merge into one card and Net Outcome goes.
        const show = (id, on) => { const el = document.getElementById(id); if (el) el.style.display = on ? '' : 'none'; };
        show('statWinsCard', !isSt); show('statLossesCard', !isSt); show('statOutcomeCard', !isSt);
        show('statWinLossCard', isSt);
        const r1 = document.getElementById('resultsRow1');
        if (r1) r1.style.gridTemplateColumns = '';
        // Simple Trader is two rows of six: Profit Factor and Net P&L move up beside Total P&L, and the
        // streak and max cards slot in before CAGR. Any other strategy puts every card back where it was.
        const card = id => document.getElementById(id)?.closest('.stat-card');
        const rtp = document.getElementById('rtpStatsRow');
        const pf = card('statProfitFactor'), net = card('statNetRs'), cagr = card('statCagr');
        const streak = card('statWinStreak'), maxpl = card('statMaxProfit');
        if (r1 && rtp && row && pf && net && cagr && streak && maxpl) {
            if (isSt) {
                r1.appendChild(pf); r1.appendChild(net);
                rtp.insertBefore(streak, cagr); rtp.insertBefore(maxpl, cagr);
            } else {
                rtp.insertBefore(pf, rtp.firstChild);
                rtp.insertBefore(net, cagr);
                row.appendChild(streak); row.appendChild(maxpl);
            }
        }
        if (isSt) {
            const t = id => document.getElementById(id)?.textContent || '0';
            const wEl = document.getElementById('statWinLossW'), lEl = document.getElementById('statWinLossL');
            if (wEl) wEl.textContent = t('statWins');
            if (lEl) lEl.textContent = t('statLosses');
        }
        if (!isSt) { setSub('statAvgWinSub', 'pts per win'); setSub('statAvgLossSub', 'pts per loss'); return; }
        const trades = ((data && data.trades) || []).filter(t => t.pnl != null)
            .slice().sort((a, b) => String(a.entry_time).localeCompare(String(b.entry_time)));
        if (!trades.length || !row) return;
        const lotVal = Math.max(1, parseFloat(document.getElementById('pcLotValue')?.value || 65));
        const rs = t => t.gross_pnl_rupees != null ? Number(t.gross_pnl_rupees) : Number(t.pnl) * lotVal * (t.lots || 1);
        const inr = v => (v < 0 ? '-' : '') + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN');
        const vals = trades.map(rs);
        const wins = vals.filter(v => v > 0), losses = vals.filter(v => v < 0);

        const avgW = wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : null;
        const avgL = losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : null;
        const awEl = document.getElementById('statAvgWin'), alEl = document.getElementById('statAvgLoss');
        if (awEl) awEl.textContent = avgW != null ? '+' + inr(avgW) : '—';
        if (alEl) alEl.textContent = avgL != null ? inr(avgL) : '—';
        setSub('statAvgWinSub', 'points × lots × lot size');
        setSub('statAvgLossSub', 'points × lots × lot size');

        let w = 0, l = 0, maxW = 0, maxL = 0, cum = 0, peak = 0, peakIdx = -1, ddMax = 0, ddDays = 0;
        vals.forEach((v, i) => {
            if (v > 0) { w++; l = 0; } else if (v < 0) { l++; w = 0; } else { w = l = 0; }
            maxW = Math.max(maxW, w); maxL = Math.max(maxL, l);
            cum += v;
            if (cum > peak) { peak = cum; peakIdx = i; }
            if (peak - cum > ddMax) {
                ddMax = peak - cum;
                ddDays = new Set(trades.slice(peakIdx + 1, i + 1).map(t => String(t.entry_time).slice(0, 10))).size;
            }
        });
        const best = Math.max(...vals), worst = Math.min(...vals);
        const bestT = trades[vals.indexOf(best)], worstT = trades[vals.indexOf(worst)];
        const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
        set('statMaxDDDates', ddDays + ' drawdown day' + (ddDays === 1 ? '' : 's') + ' (peak to trough)');
        set('statWinStreak', String(maxW));
        set('statLossStreak', String(maxL));
        set('statMaxProfit', '+' + inr(best));
        set('statMaxLoss', inr(worst));
        set('statMaxProfitSub', String(bestT.entry_time).slice(0, 10));
        set('statMaxLossSub', String(worstT.entry_time).slice(0, 10));
    }

    function _displayResultsCore(data) {
        lastData = data;
        const { summary } = data;
        const isSM   = strategySelect && strategySelect.value === 'swing_momentum';
        const isSc   = strategySelect && strategySelect.value === 'second_candle';
        // 30-Min Fakeout scans many symbols at once, each already sized
        // server-side to ~₹1,00,000/entry — no Lots/Lot Value input needed
        // here, just a ₹ unit on the Row 1 Total P&L card instead of
        // "points". Row 1 shows GROSS (summary.total_gross_pnl_rupees);
        // the Net P&L (₹) card subtracts brokerage to get the real figure
        // (matches summary.total_pnl_rupees / per-trade t.pnl_rupees, both
        // already net).
        const isTmf  = strategySelect && strategySelect.value === 'thirty_min_fakeout';
        const isEma  = strategySelect && strategySelect.value === 'ema_pullback';
        // EMA Confluence Breakout's "All Stocks" run comes back shaped like the
        // TMF scan — many symbols, each trade already sized to ₹ server-side —
        // so it takes the same ₹ cards/columns instead of the points ones.
        const isEmaAll = !!(isEma && summary && summary.multi_symbol);
        // Weekly CPR Breakout runs every F&O future stock, each trade sized
        // server-side (lots × that stock's lot size) — the same ₹ cards.
        const isHast = strategySelect && strategySelect.value === 'ha_supertrend';
        // HA + SuperTrend is a many-stock ₹ run shaped like Weekly CPR Breakout's.
        const isWcb  = strategySelect && (strategySelect.value === 'weekly_cpr_breakout' || isHast);
        const isMultiSymbol = isTmf || isEmaAll || isWcb;
        // Scalp Pullback is single-symbol and points-based like the 2nd-candle
        // breakout, so it takes the same ₹ cards off its own lot inputs.
        const isSp   = strategySelect && strategySelect.value === 'scalp_pullback';
        // Pivot Confluence is single-symbol and points-based too.
        const isPc   = strategySelect && (strategySelect.value === 'pivot_confluence' || strategySelect.value === 'simpler_trader' || strategySelect.value === 'st_ema_rsi_cci' || strategySelect.value === 'st_rsi_pp_bb' || strategySelect.value === 'st_rsi_pp_bb_fut' || strategySelect.value === 'st_rsi_cci_wr_pp_bb_fut' || strategySelect.value === 'st_rsi_cpr_bb_sx' || strategySelect.value === 'st_rsi_cpr_bb_wr_sx' || strategySelect.value === 'hilega_milega' || strategySelect.value === 'ma_htf');
        // 30-Sec Option Breakout is single-symbol and points-based as well, but
        // its points are PREMIUM points of a real contract — so Lot Value here
        // is the lot size, not a ₹/index-point conversion.
        const isOb   = strategySelect && strategySelect.value === 'option_breakout';
        // 2nd-candle / Option Breakout / Scalp Pullback / Pivot Confluence / EMA Pullback share the ₹ cards, each reading its own lot inputs.
        const moneyLotsId    = isSc ? 'scLots'     : (isOb ? 'obLots'     : (isSp ? 'spLots'     : (isPc ? 'pcLots'     : 'emaLots')));
        const moneyLotValId  = isSc ? 'scLotValue' : (isOb ? 'obLotValue' : (isSp ? 'spLotValue' : (isPc ? 'pcLotValue' : 'emaLotValue')));

        // Candle Breakout booked on the option premium: the ₹ cards below are
        // unchanged (Lot Value ₹/pt multiplies either basis) — this only
        // relabels the points as premium points and shows the leg that was
        // priced.
        const isScOption = !!(isSc && summary.pnl_basis === 'option');
        lastData.is_multi_symbol = isMultiSymbol;

        // A single-symbol EMA run is sized on the CONTRACT's own lot size
        // server-side, so show that back in the Lot Value box rather than
        // leaving a hand-typed number next to ₹ figures it no longer drives.
        if (isEma && !isEmaAll && summary.lot_size) {
            const lotValEl = document.getElementById('emaLotValue');
            if (lotValEl) lotValEl.value = summary.lot_size;
            if (window.updateEmaInvestment) window.updateEmaInvestment(summary.capital_per_trade);
        }

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

        // For the multi-symbol scans this is the GROSS figure (before
        // brokerage) — the dedicated Net P&L (₹) card below is what actually
        // subtracts it. Points would be meaningless summed across symbols.
        const rsTotal = isMultiSymbol || !!summary.server_rupees;
        const pnl    = isMultiSymbol ? (summary.total_gross_pnl_rupees ?? 0)
                     : (summary.server_rupees ? (summary.total_gross_pnl_rupees ?? 0) : (summary.total_pnl ?? 0));
        const pnlEl  = document.getElementById('statTotalPnl');
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + (rsTotal ? '₹' + Math.round(pnl).toLocaleString('en-IN') : pnl.toFixed(2));
        pnlEl.className   = 'stat-card__val ' + (pnl >= 0 ? 'stat-val-green' : 'stat-val-red');
        const pnlUnitEl = document.getElementById('statTotalPnlUnit');
        if (pnlUnitEl) pnlUnitEl.textContent = summary.server_rupees
            ? `total profit, before brokerage · ${Number(summary.total_pnl).toFixed(2)} premium pts`
            : isMultiSymbol
            ? `gross, before brokerage · ${summary.total_trades || 0} entries`
            : ((isScOption || isOb) ? 'option premium points' : 'points');

        const outcomeEl = document.getElementById('statOutcome');
        outcomeEl.textContent = pnl >= 0 ? 'PROFIT' : 'LOSS';
        outcomeEl.className   = 'stat-card__val ' + (pnl >= 0 ? 'stat-val-green' : 'stat-val-red');

        // ── Row 2: per-trade cards (points strategies) ─────────────
        const rtpRow = document.getElementById('rtpStatsRow');
        if ((isSc || isOb || isSp || isPc || (isEma && !isEmaAll)) && rtpRow) {
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
            if (ddEl)    ddEl.textContent    = '₹' + Math.round(
                (isEma && summary.max_drawdown_rupees != null)
                    ? Math.abs(summary.max_drawdown_rupees)
                    : summary.server_rupees && summary.max_drawdown_rupees != null
                        ? Math.abs(summary.max_drawdown_rupees)
                        : Math.abs(dd) * vLotVal * vLots).toLocaleString('en-IN');

            const ddDatesEl = document.getElementById('statMaxDDDates');
            if (ddDatesEl) ddDatesEl.textContent = '';

            // EMA Confluence is sized server-side now — Lots × the contract's
            // own lot size, priced on the future, with the carry-forward rolls
            // and their brokerage already counted. Reading it off the Lot Value
            // box would quietly disagree with the trades table below it.
            const emaServerRs    = (isEma && summary.futures_priced) || !!summary.server_rupees;
            const totalBrokerage = emaServerRs
                ? (summary.total_brokerage || 0)
                : (isEma ? EMA_BROKERAGE_PER_ORDER * 2 : calcBrokeragePerTrade(vLots)) * (summary.total_trades || 0);
            const netRs = emaServerRs
                ? (summary.total_pnl_rupees || 0)
                : pnl * vLotVal * vLots - totalBrokerage;
            const netEl   = document.getElementById('statNetRs');
            if (netEl) {
                netEl.textContent = (netRs >= 0 ? '+' : '') + '₹' + Math.round(netRs).toLocaleString('en-IN');
                netEl.className   = 'stat-card__val ' + (netRs >= 0 ? 'stat-val-green' : 'stat-val-red');
            }
            const netSubEl = document.getElementById('statNetRsSub');
            if (netSubEl) {
                let sub = 'brok: ₹' + Math.round(totalBrokerage).toLocaleString('en-IN');
                if (summary.server_rupees) {
                    sub = `gross ₹${Math.round(summary.total_gross_pnl_rupees).toLocaleString('en-IN')} − brokerage ₹${Math.round(summary.total_brokerage).toLocaleString('en-IN')}`;
                    sub += summary.compounding
                        ? ` · compounding ₹${Math.round(summary.start_capital).toLocaleString('en-IN')} → ₹${Math.round(summary.final_balance).toLocaleString('en-IN')} · up to ${summary.max_lots_used} lots`
                        : ` · ${summary.max_lots_used} lot${summary.max_lots_used === 1 ? '' : 's'} a trade`;
                } else if (emaServerRs) {
                    sub += ` · ${summary.total_orders || 0} orders`;
                    if (summary.total_rolls) sub += ` (incl. ${summary.total_rolls} roll${summary.total_rolls > 1 ? 's' : ''})`;
                    sub += ` · ${summary.qty || 0} qty/entry`;
                }
                netSubEl.textContent = sub;
            }

            // Candle Breakout: say which basis the pts above are on. On the
            // option basis every "pts" on this screen is a PREMIUM point of a
            // modelled leg, so the model's assumptions belong next to them
            // rather than only in the checkbox's tooltip.
            // 30-Sec Option Breakout: say what the run actually covered. The
            // day count is the headline number here in a way it is nowhere else
            // — the range asks for sessions and a cold run may not afford all
            // of them, and without this the table would just look short.
            if (isOb) {
                // The sizing the trades table's ₹ columns price against —
                // frozen here so they keep agreeing with the cards above even
                // if the Lots boxes are edited before the next run.
                lastData.ob_money = { lots: vLots, lotValue: vLotVal };
                const subtitle = document.getElementById('btSubtitle');
                if (subtitle) {
                    const legs = summary.legs_with_data ?? 0;
                    let sub = 'P&L basis: option premium (recorded ATM CE/PE candles, not modelled)'
                        + `  ·  ${summary.days_scanned ?? 0} session${summary.days_scanned === 1 ? '' : 's'}`
                        + `  ·  ${legs}/${summary.legs_scanned ?? 0} contracts with data`
                        + `  ·  ${summary.both_legs_days ?? 0} day${summary.both_legs_days === 1 ? '' : 's'} both legs fired`;
                    // Why the run was quick, and — the day it is not — why not.
                    // The rule usually decides inside the first fifteen minutes,
                    // so it buys a fraction of each session; a figure near the
                    // maximum means the legs ran late and really were read in full.
                    if (summary.slices_max) {
                        const pct = Math.round(100 - (summary.slices_bought * 100 / summary.slices_max));
                        sub += `  ·  read ${summary.slices_bought}/${summary.slices_max}`
                             + ` session slices (${pct}% skipped)`;
                    }
                    subtitle.textContent = sub;
                }
            }

            if (isSc) {
                const subtitle = document.getElementById('btSubtitle');
                if (subtitle) {
                    subtitle.textContent = isScOption
                        ? `P&L basis: option premium  ·  ${summary.strike_mode === 'delta' ? '~0.90Δ strike' : 'strike priced nearest ₹250'}`
                          + `  ·  nearest weekly expiry  ·  IV ${Math.round((summary.option_iv || 0.15) * 100)}% assumed`
                          + '  ·  modelled (Black-Scholes), not a recorded chain'
                        : 'P&L basis: NIFTY index points';
                }
            }
        } else if (isMultiSymbol && rtpRow) {
            // Every field here is already sized server-side (TMF: ~₹1,00,000
            // per entry; EMA All Stocks: Lots × each stock's own lot size) —
            // no Lots/Lot Value inputs to read, unlike the isSc branch
            // above.
            rtpRow.style.display = '';

            document.getElementById('statProfitFactor').textContent =
                summary.profit_factor_rupees != null ? summary.profit_factor_rupees : '—';

            const avgWinEl  = document.getElementById('statAvgWin');
            const avgLossEl = document.getElementById('statAvgLoss');
            if (avgWinEl)  avgWinEl.textContent  = summary.avg_win_rupees  != null ? '+₹' + Math.round(summary.avg_win_rupees).toLocaleString('en-IN') : '—';
            if (avgLossEl) avgLossEl.textContent = summary.avg_loss_rupees != null ? '-₹' + Math.round(Math.abs(summary.avg_loss_rupees)).toLocaleString('en-IN') : '—';

            const dd      = summary.max_drawdown_rupees ?? 0;
            const ddPtsEl = document.getElementById('statMaxDDPts');
            const ddEl    = document.getElementById('statMaxDD');
            if (ddPtsEl) ddPtsEl.textContent = '₹';
            if (ddEl)    ddEl.textContent    = '₹' + Math.round(Math.abs(dd)).toLocaleString('en-IN');

            const ddDatesEl = document.getElementById('statMaxDDDates');
            if (ddDatesEl) ddDatesEl.textContent = '';

            // Net P&L = gross P&L (the Row 1 Total P&L card, before
            // brokerage) minus total brokerage — matches summary.total_pnl_rupees,
            // which the backend already computes the same way per-trade.
            const totalBrok = summary.total_brokerage || 0;
            const netRsTmf  = pnl - totalBrok;
            const netEl = document.getElementById('statNetRs');
            if (netEl) {
                netEl.textContent = (netRsTmf >= 0 ? '+' : '') + '₹' + Math.round(netRsTmf).toLocaleString('en-IN');
                netEl.className   = 'stat-card__val ' + (netRsTmf >= 0 ? 'stat-val-green' : 'stat-val-red');
            }
            const netSubEl = document.getElementById('statNetRsSub');
            if (netSubEl) {
                let sub = `brok: ₹${Math.round(totalBrok).toLocaleString('en-IN')} · ~₹${Math.round(summary.capital_per_trade || 100000).toLocaleString('en-IN')}/entry`;
                // The EMA scan's per-entry figure is an average across very
                // differently priced stocks, so say how many actually traded.
                if (isEmaAll || isWcb) sub += ` · ${summary.symbols_with_trades || 0}/${summary.symbols_scanned || 0} stocks`;
                if (isWcb && !isHast) sub += ` · ${summary.targets || 0} target / ${summary.stops || 0} SL / ${summary.time_exits || 0} time`;
                if (isHast) sub += ` · ${summary.partial_hits || 0} 1:R hit / ${summary.stops || 0} SL / ${summary.trend_exits || 0} trend exit · ${summary.expectancy_r}R expectancy`;
                if (isWcb && summary.pnl_basis === 'stock_option') sub += ' · ATM option P&L';
                netSubEl.textContent = sub;
            }

            const cagrEl    = document.getElementById('statCagr');
            const cagrSubEl = document.getElementById('statCagrSub');
            if (cagrEl) {
                const cagr = summary.cagr_pct;
                cagrEl.textContent = cagr != null ? (cagr >= 0 ? '+' : '') + cagr.toFixed(1) + '%' : '—';
                cagrEl.className   = 'stat-card__val ' + (cagr != null && cagr >= 0 ? 'stat-val-green' : 'stat-val-red');
            }
            if (cagrSubEl) cagrSubEl.textContent = 'annualized, on capital-per-trade';
        } else {
            if (rtpRow) rtpRow.style.display = 'none';
        }

        // Equity curve + period breakdown
        // TMF / Swing Momentum have no Lots box — TMF's trades carry their own
        // ₹ (directRupees below) and SM isn't money-scaled here, so the
        // fallbacks are inert.
        const lots2     = (isSc || isOb || isSp || isPc || isEma)
            ? Math.max(1, parseInt(document.getElementById(moneyLotsId)?.value      || 1))
            : 1;
        const lotValue2 = (isSc || isOb || isSp || isPc || isEma)
            ? Math.max(1, parseFloat(document.getElementById(moneyLotValId)?.value  || 65))
            : 65;
        const isMoney     = isSc || isOb || isSp || isPc || isTmf || isEma || isWcb;
        // Each multi-symbol trade already carries its own sized pnl_rupees —
        // renderEquityCurve/groupByPeriod use that directly when present,
        // ignoring lots2/lotValue2.
        // A single-symbol EMA run is sized server-side too now, so its trades
        // carry their own pnl_rupees — same as a multi-symbol scan's, and read
        // directly rather than re-derived from the Lots / Lot Value boxes.
        const directRupees = isMultiSymbol || !!(isEma && summary.futures_priced) || !!summary.server_rupees;
        const investment2 = summary.server_rupees
            ? summary.start_capital
            : (isMultiSymbol || (isEma && summary.capital_per_trade))
                ? (summary.capital_per_trade || 100000)
                : lots2 * 50000;
        renderEquityCurve(data.trades, isMoney, lots2, lotValue2, investment2);

        // Store for period tab re-renders
        _periodTrades   = data.trades;
        _periodIsMoney    = isMoney;
        _periodLots     = lots2;
        _periodLotValue = lotValue2;
        _periodUseDirectRupees = directRupees;
        const activePeriod = document.querySelector('.period-tab.active')?.dataset.period || 'monthly';
        renderPeriodBreakdown(data.trades, isMoney, lots2, lotValue2, activePeriod, directRupees);

        renderTable();
        resultsArea.style.display = 'block';
        const btTradesSec2   = document.getElementById('btTradesSection');
        const btPlaceholder2 = document.getElementById('btRightPlaceholder');
        if (btTradesSec2)   btTradesSec2.style.display   = '';
        if (btPlaceholder2) btPlaceholder2.style.display = 'none';
    }

    // ── Chart theming ─────────────────────────────────────────────────
    // Same text/grid tones as OIP_CHART_THEMES (oi_profile.js) — the app's
    // established pattern for theming a chart library that can't read CSS
    // custom properties directly.
    const BT_CHART_THEME = {
        light:  { tick: '#374151', grid: 'rgba(15, 23, 42, 0.05)',   gridZero: 'rgba(15, 23, 42, 0.25)' },
        dark:   { tick: '#94a3b8', grid: 'rgba(255, 255, 255, 0.06)', gridZero: 'rgba(255, 255, 255, 0.25)' },
        forest: { tick: '#6ba88f', grid: 'rgba(16, 185, 129, 0.08)', gridZero: 'rgba(16, 185, 129, 0.3)' },
        cream:  { tick: '#7c7267', grid: 'rgba(180, 83, 9, 0.06)',   gridZero: 'rgba(180, 83, 9, 0.3)' },
        ocean:  { tick: '#475569', grid: 'rgba(2, 132, 199, 0.06)',  gridZero: 'rgba(2, 132, 199, 0.3)' },
    };
    function _btChartColors() {
        return BT_CHART_THEME[window.AppTheme.getActiveTheme()] || BT_CHART_THEME.ocean;
    }

    // ── Equity Curve ────────────────────────────────────────────────
    let _equityChart = null;
    let _lastEquityArgs = null;   // cached args so a theme switch can redraw in place
    let _lastSmEquityArgs = null;

    function renderEquityCurve(trades, isMoney, lots, lotValue, investment) {
        const section = document.getElementById('equityCurveSection');
        if (!section || !trades || trades.length === 0) {
            if (section) section.style.display = 'none';
            return;
        }
        _lastEquityArgs = { trades, isMoney, lots, lotValue, investment };
        _lastSmEquityArgs = null;
        const chartColors = _btChartColors();

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
            const tradeRs = (t.pnl_rupees != null)
                ? t.pnl_rupees
                : (isMoney ? Math.round((t.pnl || 0) * lotValue * lots) : (t.pnl || 0));
            portfolio += tradeRs;
            labels.push('T' + (idx + 1));
            chartData.push(Math.round(portfolio));
            pointColors.push(tradeRs >= 0 ? '#00c853' : '#ff1744');
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
                        ticks: { maxTicksLimit: 15, color: chartColors.tick, font: { size: 11 }, autoSkip: true },
                        grid:  { color: chartColors.grid },
                    },
                    y: {
                        ticks: { color: chartColors.tick, font: { size: 11 }, callback: fmtY },
                        grid:  { color: chartColors.grid },
                    }
                }
            }
        });

        section.style.display = '';
    }

    // ── Period P&L Breakdown ─────────────────────────────────────────
    let _periodChart = null;
    let _periodTrades = [];
    let _periodIsMoney = false;
    let _periodLots = 1;
    let _periodLotValue = 75;
    let _periodUseDirectRupees = false;

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
            else if (period === 'quarterly')  key = `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`;
            else if (period === 'halfyearly') key = `${d.getFullYear()}-H${d.getMonth() < 6 ? 1 : 2}`;
            else                           key = `${d.getFullYear()}`;
            if (!groups[key]) groups[key] = { pnl: 0, pnlRupees: 0, wins: 0, losses: 0 };
            groups[key].pnl       += (t.pnl || 0);
            groups[key].pnlRupees += (t.pnl_rupees != null ? t.pnl_rupees : (t.pnl || 0));
            if ((t.pnl || 0) > 0) groups[key].wins++;
            else                  groups[key].losses++;
        });
        return groups;
    }

    // Compact number formatter for bar labels
    function fmtCompact(v, isMoney) {
        const abs  = Math.abs(v);
        const sign = v >= 0 ? '+' : '−';
        if (isMoney) {
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

    function renderPeriodBreakdown(trades, isMoney, lots, lotValue, period, useDirectRupees) {
        const section = document.getElementById('periodBreakdownSection');
        const chartColors = _btChartColors();
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
            if (period === 'quarterly' || period === 'halfyearly') {
                const [y, p] = k.split('-');               // 2026-Q3 → "Q3 '26"
                return `${p} '${y.slice(2)}`;
            }
            return k;
        });

        const values = keys.map(k => {
            if (useDirectRupees) return Math.round(groups[k].pnlRupees);
            const raw = groups[k].pnl;
            return isMoney ? Math.round(raw * lotValue * lots) : Math.round(raw * 100) / 100;
        });

        const meta = keys.map(k => groups[k]);  // { pnl, wins, losses }

        const bgColors  = values.map(v => v >= 0 ? 'rgba(34,197,94,.20)'  : 'rgba(239,68,68,.20)');
        const brdColors = values.map(v => v >= 0 ? 'rgba(34,197,94,.90)'  : 'rgba(239,68,68,.90)');

        const canvas = document.getElementById('periodBreakdownChart');
        if (!canvas) return;
        if (_periodChart) { _periodChart.destroy(); _periodChart = null; }

        // Give every bar a minimum pixel width so long histories (e.g. Daily
        // over years) scroll horizontally instead of squeezing into
        // illegible slivers — the wrap has overflow-x:auto, the inner div
        // is what actually grows past it.
        const inner = document.getElementById('periodChartInner');
        if (inner) {
            const MIN_BAR_PX = 34;
            const wrapWidth  = inner.parentElement.clientWidth;
            inner.style.minWidth = Math.max(wrapWidth, keys.length * MIN_BAR_PX) + 'px';
        }

        const fmt = v => fmtCompact(v, isMoney);

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
                                    ' P&L: ' + (v >= 0 ? '+' : '') + (isMoney
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
                        ticks: { font: { size: 9, weight: '500' }, color: chartColors.tick }
                    },
                    y: {
                        grid: {
                            color: ctx => ctx.tick.value === 0
                                ? chartColors.gridZero
                                : chartColors.grid,
                            lineWidth: ctx => ctx.tick.value === 0 ? 1.5 : 1,
                        },
                        ticks: {
                            font: { size: 9 }, color: chartColors.tick,
                            callback: v => {
                                if (v === 0) return '0';
                                const abs = Math.abs(v);
                                const s   = v < 0 ? '−' : '';
                                if (isMoney) {
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
            renderPeriodBreakdown(_periodTrades, _periodIsMoney, _periodLots, _periodLotValue, btn.dataset.period, _periodUseDirectRupees);
        });
    });

    // Redraw whichever charts are on screen when the app theme changes —
    // Chart.js bakes colors into its config at creation time, so a CSS
    // variable swap alone doesn't repaint an existing canvas.
    window.addEventListener('themechanged', () => {
        if (_lastEquityArgs) {
            const a = _lastEquityArgs;
            renderEquityCurve(a.trades, a.isMoney, a.lots, a.lotValue, a.investment);
        } else if (_lastSmEquityArgs) {
            const a = _lastSmEquityArgs;
            _renderSmEquityCurve(a.curve, a.investment);
        }
        if (_periodTrades && _periodTrades.length) {
            const activePeriod = document.querySelector('.period-tab.active')?.dataset.period || 'monthly';
            renderPeriodBreakdown(_periodTrades, _periodIsMoney, _periodLots, _periodLotValue, activePeriod, _periodUseDirectRupees);
        }
    });

    const TRADES_COLS = [
        { key: 'entry_time', label: 'Entry Time', sortable: true,
          // Dates sort chronologically, not lexically — same fix DataGrid's
          // numeric-aware compare already gives every other date column.
          sortValue: t => new Date(t.entry_time).getTime(),
          format: v => formatDate(v) },
        { label: 'Type', sortable: true,
          sortValue: t => t.direction || t.type || '-',
          render: (_, t) => {
              const dir = t.direction || t.type || '-';
              const isLong = dir === 'BUY' || dir === 'Long' || dir === 'long';
              return DataGrid.badge(dir, isLong ? 'pos' : 'neg');
          } },
        { key: 'entry_price', label: 'Entry Price', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'exit_time', label: 'Exit Time', sortable: true,
          sortValue: t => new Date(t.exit_time).getTime(),
          format: v => formatDate(v) },
        { key: 'exit_price', label: 'Exit Price', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { label: 'Result', sortable: true,
          sortValue: t => t.exit_reason || t.result || '-',
          format: (_, t) => t.exit_reason || t.result || '-' },
        { key: 'pnl', label: 'P&L', sortable: true, strong: true,
          format: v => (v || 0).toFixed(2), tone: DataGrid.sign },
    ];

    // Weekday of the entry — read off the entry date itself, so it sorts with it.
    const DAY_COL = { label: 'Day', sortable: true,
        sortValue: t => new Date(t.entry_time).getTime(),
        format: (_, t) => {
            const d = new Date(t.entry_time);
            return isNaN(d) ? '--' : d.toLocaleDateString('en-IN', { weekday: 'short' });
        } };

    // The multi-symbol scans (30-Min Fakeout across every F&O futures
    // stock/index, EMA Confluence Breakout across All Stocks) size every
    // entry server-side — Symbol/Quantity columns the single-symbol
    // strategies don't need, SL/Target Price columns (both engines return
    // sl_price/target_price per trade), and a ₹-sized P&L instead of the
    // generic raw-points column.
    const MULTI_SYMBOL_TRADES_COLS = [
        { key: 'symbol', label: 'Symbol', sortable: true },
        ...TRADES_COLS.slice(0, -1),
        { key: 'sl_price', label: 'SL Price', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'target_price', label: 'Target Price', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'qty', label: 'Quantity', sortable: true },
        { key: 'sl_risk_rupees', label: 'SL Risk (₹)', sortable: true,
          format: v => '₹' + Math.round(v || 0).toLocaleString('en-IN') },
        { key: 'brokerage', label: 'Brokerage (₹)', sortable: true,
          format: v => '-₹' + Math.round(v || 0).toLocaleString('en-IN') },
        { key: 'pnl_rupees', label: 'P&L (₹)', sortable: true, strong: true,
          format: v => '₹' + Math.round(v || 0).toLocaleString('en-IN'), tone: DataGrid.sign },
    ];

    // Weekly CPR Breakout booked on the stock's option: the Entry/Exit Price
    // columns stay the STOCK's (the signal), these show the contract bought
    // and its premiums, and the stock's own move beside the option P&L.
    const WCB_OPTION_COLS = [
        { key: 'option', label: 'Option', sortable: true },
        { key: 'entry_premium', label: 'Entry Prem', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'exit_premium', label: 'Exit Prem', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'stock_pnl', label: 'Stock P&L', sortable: true,
          format: v => (v || 0).toFixed(2), tone: DataGrid.sign },
    ];

    // Candle Breakout run on the option basis. The index Entry/Exit Price
    // columns above still apply — the signal, SL and Target are index levels —
    // so these say which leg was priced against them and what it cost, and keep
    // the index result visible next to the premium P&L the row is booked on.
    const SC_OPTION_COLS = [
        { label: 'Leg', sortable: true,
          sortValue: t => Number(t.strike) || 0,
          format: (_, t) => t.strike ? `${t.strike} ${t.option_type || ''}`.trim() : '—' },
        { key: 'entry_premium', label: 'Entry Prem', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'exit_premium', label: 'Exit Prem', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'index_pnl', label: 'Index P&L', sortable: true,
          format: v => (v || 0).toFixed(2), tone: DataGrid.sign },
    ];

    // 30-Sec Option Breakout is option-native: every price on a row is a
    // PREMIUM, so the generic Entry/Exit Price columns are relabelled rather
    // than joined by a second pair. The Type column goes too — both legs are
    // BOUGHT, so "Long" on every row says nothing — and the leg that replaces
    // it carries the same bullish/bearish colour Type used to: a CE is the
    // upside bet, a PE the downside one. The tail turns those premium points
    // into money at the run's own sizing: gross ₹, the round-trip brokerage,
    // and the net the row actually banked.
    const OB_TRADES_COLS = [
        TRADES_COLS[0],                                     // Entry Time
        { label: 'Strike', sortable: true,
          sortValue: t => `${t.option_type || ''}${t.strike || 0}`,
          render: (_, t) => DataGrid.badge(
              `${t.strike || '—'} ${t.option_type || ''}`.trim(),
              t.option_type === 'PE' ? 'neg' : 'pos'),
          // Where the strike came from, which is the one input the row cannot
          // show: the session open picked it off the ladder.
          title: (_, t) => (t.spot_open ? `Open ${t.spot_open}` : '')
                         + (t.expiry ? `  ·  expiry ${t.expiry}` : '') },
        { ...TRADES_COLS[2], label: 'Entry Prem' },
        TRADES_COLS[3],                                     // Exit Time
        { ...TRADES_COLS[4], label: 'Exit Prem' },
        { key: 'sl_price', label: 'SL', sortable: true,
          format: v => (v || 0).toFixed(2) },
        { key: 'target_price', label: 'Target', sortable: true,
          format: v => (v || 0).toFixed(2) },
        TRADES_COLS[5],                                     // Result
        { ...TRADES_COLS[6], label: 'P&L (Points)' },
        { label: 'P&L (₹)', sortable: true,
          sortValue: t => _obRowMoney(t).gross,
          format: (_, t) => DataGrid.inr(_obRowMoney(t).gross),
          tone:   (_, t) => DataGrid.sign(_obRowMoney(t).gross) },
        { label: 'Brokerage (₹)', sortable: true,
          sortValue: t => _obRowMoney(t).brokerage,
          format: (_, t) => '-₹' + Math.round(_obRowMoney(t).brokerage).toLocaleString('en-IN'),
          title: 'Flat NIFTY round-trip charge (entry + exit) for the lot count in the Lots box' },
        { label: 'Final P&L (₹)', sortable: true, strong: true,
          sortValue: t => _obRowMoney(t).net,
          format: (_, t) => DataGrid.inr(_obRowMoney(t).net),
          tone:   (_, t) => DataGrid.sign(_obRowMoney(t).net) },
    ];

    // What one row's premium points are worth in money. The sizing comes from
    // the run that produced the table (frozen into `lastData.ob_money` when the
    // ₹ cards were filled) rather than from whatever the Lots boxes hold
    // now — a re-sort must not quietly reprice the table against cards that
    // still read the old lot count. Brokerage is the flat NIFTY round trip for
    // that lot count, the same charge the Net P&L card and the optimiser board
    // subtract, so Final P&L is the only figure here a broker statement matches.
    function _obRowMoney(t) {
        const { lots, lotValue } = (lastData && lastData.ob_money) || _obOptMoney();
        const gross     = (Number(t.pnl) || 0) * lotValue * lots;
        const brokerage = calcBrokeragePerTrade(lots);
        return { gross, brokerage, net: gross - brokerage };
    }

    // EMA Confluence books its money on the monthly future, so Entry/Exit
    // Price above are the CONTRACT's — these say which contract that was, how
    // many times the position was carried forward, and what those extra orders
    // cost. `contract` differs from `exit_contract` exactly when it rolled.
    const EMA_FUTURES_COLS = [
        // Plain text only: DataGrid escapes whatever `format` returns (markup
        // belongs in `render`), and the note this cell carries is a tooltip,
        // which the grid puts on the cell itself via `title`.
        { label: 'Contract', sortable: true,
          sortValue: t => t.contract || '',
          format: (_, t) => {
              const months = t.rolls ? `${t.contract} → ${t.exit_contract}` : (t.contract || '—');
              // A trade whose contract history had a hole — or whose contract
              // predates a corporate action the spot series is adjusted for —
              // is priced on the spot scale end to end rather than mixing the
              // two, so say so: its P&L would otherwise read as the future's.
              return t.futures_priced === false ? `${months} · spot` : months;
          },
          title: (_, t) => t.futures_priced === false
              ? 'Priced on the spot scale — no usable futures history for this contract '
                + '(the broker serves none, or its quotes predate a corporate action '
                + 'the daily series is adjusted for)'
              : '' },
        { key: 'rolls', label: 'Rolls', sortable: true,
          format: v => v || 0 },
        { key: 'brokerage', label: 'Brokerage (₹)', sortable: true,
          format: v => '-₹' + Math.round(v || 0).toLocaleString('en-IN') },
        { key: 'pnl_rupees', label: 'P&L (₹)', sortable: true, strong: true,
          format: v => '₹' + Math.round(v || 0).toLocaleString('en-IN'), tone: DataGrid.sign },
    ];

    // A carried-forward EMA trade is ONE setup but several round trips — the
    // near contract is booked out and the far one opened at each roll. The row
    // above shows the setup (first entry, last exit, total P&L), which on its
    // own reads as a single fill at prices that never connect; this is the
    // breakdown behind it, one line per contract actually held.
    const _LEG_TONE = { TARGET: 'pos', SL: 'neg', ROLL: 'info', DATA_END: 'warn' };

    function _emaLegsDetail(t) {
        if (!t || !Array.isArray(t.legs) || t.legs.length < 2) return '';
        const esc = DataGrid.escape;
        const px  = v => Number(v ?? 0).toFixed(2);
        const pts = v => (Number(v) >= 0 ? '+' : '') + Number(v ?? 0).toFixed(2);
        const rs  = v => (Number(v) >= 0 ? '+₹' : '-₹')
                       + Math.abs(Math.round(v || 0)).toLocaleString('en-IN');
        const tone = v => Number(v) >= 0 ? 'dg-pos' : 'dg-neg';
        // A roll that cost nothing is not a gain — a spot-priced trade has no
        // spread at all, and colouring its 0.00 green reads as one.
        const rollTone = v => Number(v) === 0 ? 'dg-muted' : tone(v);

        // "01/10 → 23/10/2025", with the year carried only by the end date when
        // both fall in the same one. One column instead of two, and the eye
        // reads a holding period rather than two unrelated dates.
        const parts = v => String(v ?? '').slice(0, 10).split('-');
        const held = (from, to) => {
            const a = parts(from), b = parts(to);
            if (a.length < 3 || b.length < 3) return '—';
            const end = `${b[2]}/${b[1]}/${b[0]}`;
            return (a[0] === b[0] ? `${a[2]}/${a[1]}` : `${a[2]}/${a[1]}/${a[0]}`) + ' → ' + end;
        };

        // What a roll actually cost. Held continuously the trade would have
        // made sign×(last exit − first entry); each roll books out at the near
        // contract's price and back in at the far one's, and that gap is the
        // difference. It is the number this breakdown exists to show, and it
        // appears nowhere else in the UI.
        const sign = (t.type === 'Short' || t.direction === 'SELL') ? -1 : 1;
        const rollDelta = (leg, i) => i === 0
            ? null
            : -sign * (Number(leg.entry_price) - Number(t.legs[i - 1].exit_price));

        let rollTotal = 0;
        const rows = t.legs.map((leg, i) => {
            const delta = rollDelta(leg, i);
            if (delta !== null) rollTotal += delta;
            const reason = String(leg.reason || '');
            return `<tr>
              <td class="dg-legs-contract">${esc(leg.contract)}</td>
              <td class="dg-legs-held">${esc(held(leg.entry_date, leg.exit_date))}</td>
              <td class="dg-legs-num">${esc(px(leg.entry_price))}</td>
              <td class="dg-legs-num">${esc(px(leg.exit_price))}</td>
              <td class="dg-legs-why">${DataGrid.badge(reason, _LEG_TONE[reason] || 'neutral')}</td>
              <td class="dg-legs-num ${delta === null ? 'dg-muted' : rollTone(delta)}">${
                  delta === null ? '—' : esc(pts(delta))}</td>
              <td class="dg-legs-num ${tone(leg.pnl)}">${esc(pts(leg.pnl))}</td>
              <td class="dg-legs-num ${tone(leg.pnl_rupees)}">${esc(rs(leg.pnl_rupees))}</td>
            </tr>`;
        }).join('');

        // Every order is ₹1,000 — entry, exit, and both legs of every roll — so
        // the foot reconciles leg P&L → gross → the net figure the parent row
        // shows, which is the sum a reader opens this to check. The labels sit
        // in the badge column, hard against their own numbers, rather than
        // stranded at the far left behind a wide colspan.
        const orders = t.orders || t.legs.length * 2;
        const gross  = Number(t.gross_pnl_rupees ?? 0);
        const net    = Number(t.pnl_rupees ?? 0);
        const chain  = t.legs.map(l => esc(l.contract))
                             .join('<span class="dg-legs-arrow">→</span>');

        return `<div class="dg-legs-wrap">
            <div class="dg-legs-hdr">
              <span class="dg-legs-chain">${chain}</span>
              <span class="dg-legs-meta">${t.rolls} roll${t.rolls === 1 ? '' : 's'}
                · qty ${(Number(t.qty) || 0).toLocaleString('en-IN')}
                · ${orders} orders × ₹${EMA_BROKERAGE_PER_ORDER.toLocaleString('en-IN')}</span>
            </div>
            <table class="dg-legs">
              <thead><tr>
                <th>Contract</th><th>Held</th>
                <th class="dg-legs-num">In</th><th class="dg-legs-num">Out</th>
                <th class="dg-legs-why">Closed</th>
                <th class="dg-legs-num" title="What the roll cost: booked out of the near contract and back into the far one at that session's prices">Roll</th>
                <th class="dg-legs-num">Pts</th><th class="dg-legs-num">P&amp;L</th>
              </tr></thead>
              <tbody>${rows}</tbody>
              <tfoot>
                <tr class="dg-legs-total">
                  <td colspan="4"></td>
                  <td class="dg-legs-why">Total</td>
                  <td class="dg-legs-num ${rollTone(rollTotal)}">${esc(pts(rollTotal))}</td>
                  <td class="dg-legs-num ${tone(t.pnl)}">${esc(pts(t.pnl))}</td>
                  <td class="dg-legs-num ${tone(gross)}">${esc(rs(gross))}</td>
                </tr>
                <tr class="dg-legs-net">
                  <td colspan="7">less ₹${Math.round(t.brokerage || 0).toLocaleString('en-IN')} brokerage</td>
                  <td class="dg-legs-num ${tone(net)}">${esc(rs(net))}</td>
                </tr>
              </tfoot>
            </table>
        </div>`;
    }

    function renderTable() {
        if (!lastData || !lastData.trades) return;
        if (lastData.is_swing_momentum) { _renderSmTable(lastData.trades); return; }

        const multi = !!lastData.is_multi_symbol;
        const futures = !!(lastData.summary && lastData.summary.futures_priced);
        let columns = multi ? MULTI_SYMBOL_TRADES_COLS : TRADES_COLS;
        // The multi-symbol column set already carries qty/brokerage/₹ P&L, so
        // it only needs the contract pair spliced in ahead of them.
        if (multi && lastData.summary && lastData.summary.pnl_basis === 'stock_option') {
            // Option legs go in ahead of Quantity; "SL Risk" on a bought
            // option is the premium paid, so it is relabelled.
            columns = [...columns.slice(0, -4), ...WCB_OPTION_COLS,
                       columns[columns.length - 4],
                       { ...columns[columns.length - 3], label: 'Premium Paid (₹)' },
                       ...columns.slice(-2)];
        } else if (futures) {
            columns = multi
                ? [...columns.slice(0, -4), EMA_FUTURES_COLS[0], EMA_FUTURES_COLS[1], ...columns.slice(-4)]
                : [...columns, ...EMA_FUTURES_COLS];
        } else if (!multi && lastData.summary && lastData.summary.pnl_basis === 'option_premium') {
            // Option-native run — its own column set entirely, not a splice.
            columns = OB_TRADES_COLS;
        } else if (!multi && lastData.summary && lastData.summary.pnl_basis === 'option') {
            // Leg / premiums / index P&L go in front of the P&L column, which
            // stays last and is now the premium's.
            const pnlCol = columns[columns.length - 1];
            columns = [columns[0], DAY_COL, ...columns.slice(1, -1), ...SC_OPTION_COLS,
                       { ...pnlCol, label: 'Opt P&L' },
                       ...(lastData.summary.server_rupees ? [
                           { key: 'lots', label: 'Lots', sortable: true },
                           { key: 'gross_pnl_rupees', label: 'Total P&L (₹)', sortable: true,
                             format: DataGrid.inr, tone: DataGrid.sign },
                           { key: 'brokerage', label: 'Brokerage (₹)', sortable: true,
                             format: DataGrid.inr },
                           { key: 'pnl_rupees', label: 'Net P&L (₹)', sortable: true, strong: true,
                             format: DataGrid.inr, tone: DataGrid.sign },
                           ...(lastData.summary.compounding ? [
                               { key: 'balance_after', label: 'Balance (₹)', sortable: true,
                                 format: DataGrid.inr }] : []),
                       ] : [])];
        }
        DataGrid.mountSortable('tradesGrid', {
            rows: lastData.trades,
            columns,
            empty: 'No trades generated',
            defaultSort: multi ? { key: 'symbol', dir: 'asc' } : { key: 'exit_time', dir: 'desc' },
            // Only rolled trades get one; a single-contract trade's row
            // already tells the whole story.
            detail: futures ? _emaLegsDetail : undefined,
        });
    }

    function formatDate(dateStr) {
        if (!dateStr) return '--';
        const d = new Date(dateStr);
        return d.toLocaleString();
    }

    // ── Expand / collapse ────────────────────────────────────────────
    // Any element carrying [data-collapse="<selector>"] becomes a clickable
    // header that toggles the matching element (searched inside its parent,
    // then document-wide). A ▾/▸ chevron reflects state. Idempotent — safe to
    // call repeatedly, including on dynamically-rendered content.
    function initCollapsibles(root) {
        (root || document).querySelectorAll('[data-collapse]').forEach(h => {
            if (h._collapseWired) return;
            h._collapseWired = true;
            h.classList.add('collapsible-h');
            // Resolve the target once (searched inside the parent, then
            // document-wide). Persisted elements keep this reference valid.
            const sel = h.dataset.collapse;
            const target = (h.parentElement && h.parentElement.querySelector(sel))
                         || document.querySelector(sel);
            const chev = document.createElement('span');
            chev.className = 'collapse-chev';
            // Chevron reflects the *initial* state, so a section that starts
            // collapsed (has .collapsed-hide in markup) shows ▸.
            chev.textContent = (target && target.classList.contains('collapsed-hide')) ? '▸' : '▾';
            // Nest the chevron inside the heading (or the header itself when
            // there's no heading) so it stays beside the title instead of being
            // pushed apart by a space-between header layout.
            const anchor = h.querySelector('h2, h3, h4, h5') || h;
            anchor.insertBefore(chev, anchor.firstChild);
            h.addEventListener('click', (e) => {
                // Ignore clicks on interactive controls living in the header.
                if (e.target.closest('button, a, input, select')) return;
                if (!target) return;
                const collapsed = target.classList.toggle('collapsed-hide');
                chev.textContent = collapsed ? '▸' : '▾';
            });
        });
    }
    initCollapsibles();

    // Programmatically collapse/expand a [data-collapse] header, keeping its
    // chevron in sync with the toggle handler wired in initCollapsibles().
    function setCollapsed(header, collapse) {
        if (!header) return;
        const sel = header.dataset.collapse;
        const target = (header.parentElement && header.parentElement.querySelector(sel))
                     || document.querySelector(sel);
        if (!target) return;
        target.classList.toggle('collapsed-hide', collapse);
        const chev = header.querySelector('.collapse-chev');
        if (chev) chev.textContent = collapse ? '▸' : '▾';
    }

    // ── Shared per-timeframe optimiser grid ────────────────────────────
    // The Candle Breakout, Scalp and Pivot optimisers each produce a best-combos grid
    // per timeframe, with the same shape: an old-style column list of
    // {label, key, fmt(r)}, a "Use this row" button, and the best row
    // highlighted while sorted by ₹ P&L descending (the default). This is
    // that grid, built once from DataGrid.mountSortable, so both call it
    // instead of carrying their own copy of the sort/highlight/click-wiring.
    //
    // `legacyCols`: the existing SC_OPT_COLS shape — kept as-is
    // rather than rewritten as DataGrid columns, since each cell's fmt(r)
    // already reads whatever fields (including derived ₹ ones) it needs.
    // `spec`: { idPrefix, isLive(row), derivedSort: {key: row=>comparable},
    //           applyFn(row), stateStore } — stateStore is the caller's own
    //           `_scOptGroupsByTf`-shaped object, kept alive so any other code
    //           reading it afterwards (there is none today, but the shape
    //           mirrors the pre-DataGrid version) still finds `.displayed`.
    function _mapLegacyOptColumns(legacyCols, spec) {
        return legacyCols.map((c, idx) => {
            if (c.label === '#') {
                return { label: '#', render: (_, r, i) => i + 1 };
            }
            if (c.label === 'Live') {
                return { label: 'Live', render: (_, r) => spec.isLive(r) ? LIVE_BADGE_HTML : '<span class="opt-live-off">—</span>' };
            }
            if (c.label === '') {
                // The "Use this row" button. `i` is this row's position in the
                // CURRENTLY DISPLAYED (sorted) order, which is exactly what the
                // container's onSorted keeps in stateStore[tf].displayed — so
                // the click handler can look the row back up by that same index.
                return { label: '', cellClass: 'opt-td-use',
                    render: (_, r, i) => `<button class="btn-opt-use" data-idx="${i}">Use</button>` };
            }
            const col = { key: c.key, label: c.label, sortable: !!c.key,
                render: (_, r) => c.fmt(r) };
            if (spec.derivedSort && spec.derivedSort[c.key]) col.sortValue = spec.derivedSort[c.key];
            return col;
        });
    }

    function _renderOptTfGrids(container, groups, legacyCols, spec) {
        Object.keys(spec.stateStore).forEach(k => delete spec.stateStore[k]);
        if (!container) return;

        container.innerHTML = groups.map((g, gi) => `
            <div class="opt-tf-block">
                <div class="opt-tf-title" data-collapse=".opt-table-wrap">${g.tf_label}
                    <span>best ${(g.results || []).length} of ${g.total}</span>
                </div>
                <div class="opt-table-wrap collapsed-hide">
                    <div id="${spec.idPrefix}-${gi}"></div>
                </div>
            </div>`).join('') || '<div class="opt-tf-title">No timeframe produced enough trades.</div>';

        const columns = _mapLegacyOptColumns(legacyCols, spec);

        groups.forEach((g, gi) => {
            const grid = document.getElementById(`${spec.idPrefix}-${gi}`);
            if (!grid) return;
            // gridEl lets refreshLiveConfigs() repaint the "Live" badge column
            // later without re-fetching or re-sorting — DataGrid.refresh() just
            // re-runs the same render with whatever _liveConfigs holds now.
            const tfState = { displayed: (g.results || []).slice(), sortState: null, gridEl: grid };
            spec.stateStore[g.tf_label] = tfState;

            DataGrid.mountSortable(grid, {
                rows: g.results || [],
                columns,
                empty: 'No combos passed the filter.',
                defaultSort: { key: 'net_pnl_inr', dir: 'desc' },
                // Highlight the leading row only while it's genuinely the
                // ₹-P&L leader — i.e. still sorted net_pnl_inr desc, matching
                // the pre-DataGrid `highlightBest` behaviour.
                rowClass: (row, i) => {
                    const st = tfState.sortState;
                    const isLeader = st && st.key === 'net_pnl_inr' && st.dir === 'desc';
                    return (i === 0 && isLeader) ? 'opt-best' : '';
                },
                onSorted: (rows, sortState) => {
                    tfState.displayed = rows;
                    tfState.sortState = sortState;
                },
            });

            // "Use" buttons: one delegated listener per grid, survives re-render.
            // Must look `tfState` up fresh via spec.stateStore[g.tf_label] on
            // every click rather than close over this call's `tfState` — a
            // later re-render (e.g. re-running "Find Best Params") replaces
            // spec.stateStore[g.tf_label] with a brand-new object (line above),
            // and this listener is only wired once, so a captured `tfState`
            // would go stale and "Use" would apply rows from the previous run.
            if (!grid.dataset.optUseWired) {
                grid.dataset.optUseWired = '1';
                const tfLabel = g.tf_label;
                grid.addEventListener('click', (e) => {
                    const btn = e.target.closest('.btn-opt-use');
                    if (!btn) return;
                    const idx = parseInt(btn.dataset.idx, 10);
                    const current = spec.stateStore[tfLabel];
                    const row = current && current.displayed[idx];
                    if (row) spec.applyFn(row);
                });
            }
        });

        initCollapsibles(container);
    }

    // ── Single-grid Optimise (Swing Momentum, EMA Confluence) ─────────
    // A single (non-per-timeframe) sortable results grid. Same # /
    // best-row-highlight / Use-button shape as the
    // per-timeframe grids in _renderOptTfGrids, just without the grouping.
    function _mountSingleOptGrid(gridId, rows, legacyCols, defaultSortKey, applyFn, derivedSort) {
        const grid = document.getElementById(gridId);
        if (!grid) return;
        const columns = _mapLegacyOptColumns(legacyCols, { isLive: () => false, derivedSort: derivedSort || {} })
            // SM has no live-algo concept here — drop the column rather
            // than render one that's permanently off.
            .filter(c => c.label !== 'Live');
        // `_displayed` lives on the grid element itself, not a closure-local
        // var — the "Use" click listener below is wired only once (guarded by
        // optUseWired) and survives every later re-render (e.g. re-running
        // "Find Best Params"), so it must always read whatever the CURRENT
        // render's rows are rather than closing over the first render's array.
        grid._displayed = rows.slice();
        grid._applyFn = applyFn;            // read by the once-wired click listener below, so a later board (another strategy) applies with ITS function
        let sortState = null;
        DataGrid.mountSortable(grid, {
            rows, columns, empty: 'No combos passed the filter.',
            defaultSort: { key: defaultSortKey, dir: 'desc' },
            rowClass: (row, i) => {
                const isLeader = sortState && sortState.key === defaultSortKey && sortState.dir === 'desc';
                return [(i === 0 && isLeader) ? 'opt-best' : '', row && row._current ? 'opt-default' : ''].join(' ').trim();
            },
            onSorted: (r, st) => { grid._displayed = r; sortState = st; },
        });
        if (!grid.dataset.optUseWired) {
            grid.dataset.optUseWired = '1';
            grid.addEventListener('click', (e) => {
                const btn = e.target.closest('.btn-opt-use');
                if (!btn) return;
                const row = grid._displayed[parseInt(btn.dataset.idx, 10)];
                if (row && grid._applyFn) grid._applyFn(row);
            });
        }
    }

    // ── EMA Confluence Breakout Optimise (Find Best Params) ─────────────────
    // Only two real free params — Direction and Target % — so a single flat
    // grid (no per-timeframe grouping) sweeping both, same shape as SM's.
    // The sweep is ranked on NET ₹ now — futures fills, carry-forward rolls and
    // ₹1,000 per order — so the money columns are the ones that decide, and
    // Win% is the net-of-cost one (a combo that wins on points and loses on
    // brokerage is not a winner). Points stay visible beside them.
    const _emaRs = v => `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v || 0).toLocaleString('en-IN')}</span>`;
    const EMA_OPT_COLS = [
        { label: '#',             key: null,            fmt: (r, i) => i + 1 },
        { label: 'Direction',     key: 'direction',      fmt: r => r.direction === 'both' ? 'Buy & Sell' : (r.direction === 'long' ? 'Buy only' : 'Sell only') },
        { label: 'Target %',      key: 'target_pct',     fmt: r => `${r.target_pct}%` },
        { label: 'Trades',        key: 'total_trades',   fmt: r => r.total_trades },
        { label: 'Win%',          key: 'win_rate',       fmt: r => `${r.win_rate != null ? r.win_rate.toFixed(0) : '0'}%` },
        { label: 'Net P&L (₹)',   key: 'total_pnl_rupees', fmt: r => r.total_pnl_rupees != null ? _emaRs(r.total_pnl_rupees) : '—' },
        { label: 'Brokerage (₹)', key: 'total_brokerage',  fmt: r => r.total_brokerage != null ? `<span class="pnl-negative">-₹${Math.round(r.total_brokerage).toLocaleString('en-IN')}</span>` : '—' },
        { label: 'Rolls',         key: 'rolls',          fmt: r => r.rolls != null ? r.rolls : '—' },
        { label: 'Net P&L (pts)', key: 'total_pnl',      fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1)} pts</span>` },
        { label: 'Prof. Factor',  key: 'profit_factor',  fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD (₹)',    key: 'max_drawdown_rupees', fmt: r => r.max_drawdown_rupees != null ? `<span class="pnl-negative">-₹${Math.round(Math.abs(r.max_drawdown_rupees)).toLocaleString('en-IN')}</span>` : `<span class="pnl-negative">${r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—'}</span>` },
        { label: '',              key: null,            fmt: () => '' },
    ];

    function applyEmaOptResult(r) {
        const direction = document.getElementById('emaDirection');
        const target    = document.getElementById('emaTargetPct');
        if (direction) direction.value = r.direction;
        if (target)    target.value    = r.target_pct;
        if (window.showNotification) {
            window.showNotification(
                `Applied: ${direction ? direction.options[direction.selectedIndex].text : r.direction}  ·  Target ${r.target_pct}%  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    function renderEmaOptResults(data) {
        const panel     = document.getElementById('emaOptimisePanel');
        const metaEl    = document.getElementById('emaOptMeta');
        const recalcBtn = document.getElementById('emaRecalcOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol}`;
            if (data.futures_priced) {
                meta += ` · futures, ${data.lots || 1} lot × ${data.lot_size || 1}`
                      + ` · ₹${(data.brokerage_per_order || 1000).toLocaleString('en-IN')}/order`;
            }
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _mountSingleOptGrid('emaOptGrid', data.results || [],
            EMA_OPT_COLS,
            data.futures_priced ? 'total_pnl_rupees' : 'total_pnl',
            applyEmaOptResult, {});

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyEmaOptResult(data.best);
    }

    async function runEmaOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }
        if (symbol === ALL_STOCKS) {
            // The sweep ranks one symbol's Direction/Target combos; across the
            // universe each stock already carries its own best-known combo.
            window.showNotification('Find Best Params runs on a single symbol — pick one instead of All Stocks.', 'warning');
            return;
        }

        const panel     = document.getElementById('emaOptimisePanel');
        const recalcBtn = document.getElementById('emaRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';
        _showOptLoader('Finding best params…', 'Sweeping direction / target combos…');

        try {
            const resp = await fetch('/api/backtest/ema-pullback/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol,
                    start_date: document.getElementById('startDate').value,
                    end_date:   document.getElementById('endDate').value,
                    recalculate,
                    // The gate rejects more setups the smaller the target, so
                    // the sweep has to score under the same rule as the run.
                    require_rr: document.getElementById('emaRequireRR')?.checked ?? false,
                    // …and it ranks on ₹, so it has to be sized like the run too.
                    lots: Math.max(1, parseInt(document.getElementById('emaLots')?.value || 1)),
                })
            });
            const data = await resp.json();
            if (!data.success) { window.showNotification(data.error || 'Optimisation failed', 'error'); return; }
            renderEmaOptResults(data);
        } catch (err) {
            console.error('EMA Confluence Breakout optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
        } finally {
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
            _hideOptLoader();
        }
    }

    const emaRecalcBtn = document.getElementById('emaRecalcOptBtn');
    if (emaRecalcBtn) emaRecalcBtn.addEventListener('click', () => runEmaOptimise(true));

    // ── Candle Breakout Optimise (Find Best Params) — one grid per timeframe ───
    // Position sizing for the 2nd-Candle grid's ₹ columns, from its lot inputs
    // (defaults: 1 lot × 65 ₹/pt — the NIFTY value, same as the result cards).
    function _scOptMoney() {
        const lots     = Math.max(1, parseInt(document.getElementById('scLots')?.value    || 1));
        const lotValue = Math.max(1, parseFloat(document.getElementById('scLotValue')?.value || 65));
        return { lots, lotValue };
    }
    function _scOptBrokerage(r, lots) { return calcBrokeragePerTrade(lots) * (r.total_trades || 0); }
    function _scOptNetRs(r, lots, lotValue) { return (r.total_pnl || 0) * lotValue * lots - _scOptBrokerage(r, lots); }

    // Column spec for the per-timeframe grids (one per timeframe, with the
    // 2nd-candle params). `key` (when set) is the field the column sorts on.
    const SC_OPT_COLS = [
        { label: '#',             key: null,            fmt: (r, i) => i + 1 },
        { label: 'Live',          key: null,            fmt: r => _isScComboLive(r) ? LIVE_BADGE_HTML : '<span class="opt-live-off">—</span>' },
        { label: 'Candle',        key: 'candle_index',  fmt: r => r.candle_index },
        { label: 'Dir',           key: 'direction',     fmt: r => `<span style="white-space:nowrap">${r.direction}</span>` },
        { label: 'SL:Target',     key: 'rr_ratio',      fmt: r => `1:${r.rr_ratio}` },
        { label: 'Trades',        key: 'total_trades',  fmt: r => r.total_trades },
        { label: 'Win%',          key: 'win_rate',      fmt: r => `${r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0'}%` },
        { label: 'Net P&L (pts)', key: 'total_pnl',     fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1)} pts</span>` },
        { label: 'Net P&L (₹)',   key: 'net_pnl_inr',   fmt: r => { const { lots, lotValue } = _scOptMoney(); const v = _scOptNetRs(r, lots, lotValue); return `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN')}</span>`; } },
        { label: 'Brokerage (₹)', key: 'brokerage_inr', fmt: r => { const { lots } = _scOptMoney(); const b = _scOptBrokerage(r, lots); return `<span class="pnl-negative">-₹${Math.round(b).toLocaleString('en-IN')}</span>`; } },
        { label: 'Prof. Factor',  key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD',        key: 'max_drawdown',  fmt: r => `<span class="pnl-negative">${r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—'}</span>` },
        { label: '',              key: null,            fmt: () => '' },   // Use button
    ];

    let _scOptGroupsByTf = {};

    function renderScOptResults(data) {
        const panel     = document.getElementById('secondCandleOptimisePanel');
        const container = document.getElementById('scOptGrids');
        const metaEl    = document.getElementById('scOptMeta');
        const recalcBtn = document.getElementById('scRecalcOptBtn');

        refreshLiveConfigs();   // re-badge rows against the current live algos

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;
            meta += _scOptionBasis() ? ' · option premium basis' : ' · index basis';
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _renderOptTfGrids(container, data.timeframes || [], SC_OPT_COLS, {
            idPrefix: 'scOptGrid',
            isLive: _isScComboLive,
            applyFn: applyScOptResult,
            stateStore: _scOptGroupsByTf,
            derivedSort: {
                win_rate: r => r.total_trades ? r.wins / r.total_trades : 0,
                net_pnl_inr: r => { const { lots, lotValue } = _scOptMoney(); return _scOptNetRs(r, lots, lotValue); },
                brokerage_inr: r => { const { lots } = _scOptMoney(); return _scOptBrokerage(r, lots); },
            },
        });

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyScOptResult(data.best);
    }

    // Abort any running 2nd-Candle optimise (pending request + polling loop).
    function cancelScOptimise() {
        _scOptRun += 1;
        if (_scOptAbort) {
            try { _scOptAbort.abort(); } catch (e) { /* noop */ }
            _scOptAbort = null;
        }
        _hideOptLoader();
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

        cancelScOptimise();
        const myRun      = _scOptRun;
        const controller = new AbortController();
        _scOptAbort      = controller;
        _showOptLoader('Finding best params…', 'Sweeping candle / R:R combos across timeframes…');

        const exitTime = (document.getElementById('scExitTime')?.value || '15:25').split(':');
        try {
            const resp = await fetch('/api/backtest/second-candle/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate').value,
                    end_date:    document.getElementById('endDate').value,
                    exit_hour:   parseInt(exitTime[0] || 15),
                    exit_minute: parseInt(exitTime[1] || 25),
                    recalculate: recalculate,
                    option_pnl:  _scOptionBasis(),
                })
            });
            const data = await resp.json();
            if (myRun !== _scOptRun) return; // superseded
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            if (data.from_cache) {
                _scOptAbort = null;
                _hideOptLoader();
                renderScOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            _pollScOptimise(data.task_id, activeBtn, origText, Date.now(), myRun);
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            console.error('2nd Candle optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
            _hideOptLoader();
        }
    }

    function _pollScOptimise(taskId, activeBtn, origText, startMs, myRun) {
        const MAX_WAIT_MS = 10 * 60 * 1000;
        function tick() {
            if (myRun !== _scOptRun) return;
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;
            _showOptLoader('Finding best params…', `${elapsed}s elapsed`);
            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            fetch(`/api/backtest/second-candle/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _scOptRun) return;
                    if (data.status === 'running') { setTimeout(tick, 2000); return; }
                    _scOptAbort = null;
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    _hideOptLoader();
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    renderScOptResults(data);
                })
                .catch(err => {
                    if (myRun !== _scOptRun) return;
                    console.error('2nd Candle poll error:', err);
                    setTimeout(tick, 3000);
                });
        }
        setTimeout(tick, 2000);
    }

    function applyScOptResult(r) {
        const ci  = document.getElementById('scCandleIndex');
        const rr  = document.getElementById('scRrRatio');
        const dir = document.getElementById('scDirection');
        const intervalSel = document.getElementById('interval');
        if (ci) ci.value = r.candle_index;
        if (rr) rr.value = r.rr_ratio;
        if (dir) dir.value = r.direction;
        // Winning timeframe → main interval dropdown, so a follow-up single
        // backtest reproduces the optimised run.
        if (intervalSel && r.interval) intervalSel.value = r.interval;
        if (window.showNotification) {
            const tfStr = r.tf_label ? `${r.tf_label} · ` : '';
            window.showNotification(
                `Applied: ${tfStr}Candle ${r.candle_index}  ·  1:${r.rr_ratio}  ·  ${r.direction}  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    const scRecalcBtn = document.getElementById('scRecalcOptBtn');
    if (scRecalcBtn) scRecalcBtn.addEventListener('click', () => runScOptimise(true));

    // ── Scalp Pullback Optimise (Find Best Params) — one grid per timeframe ───
    // Same shape as the Candle Breakout sweep above. No Live column: this
    // strategy is backtest-only, with no live algo to badge rows against.
    function _spOptMoney() {
        const lots     = Math.max(1, parseInt(document.getElementById('spLots')?.value    || 1));
        const lotValue = Math.max(1, parseFloat(document.getElementById('spLotValue')?.value || 65));
        return { lots, lotValue };
    }
    function _spOptBrokerage(r, lots) { return calcBrokeragePerTrade(lots) * (r.total_trades || 0); }
    function _spOptNetRs(r, lots, lotValue) { return (r.total_pnl || 0) * lotValue * lots - _spOptBrokerage(r, lots); }

    const SP_OPT_COLS = [
        { label: '#',             key: null,            fmt: (r, i) => i + 1 },
        { label: 'Pullback To',   key: 'touch_level',   fmt: r => r.touch_level === 'ema20' ? '20 EMA' : (r.touch_level === 'ema10' ? '10 EMA' : 'Either') },
        { label: 'Dir',           key: 'direction',     fmt: r => `<span style="white-space:nowrap">${r.direction}</span>` },
        { label: 'SL:Target',     key: 'rr_ratio',      fmt: r => `1:${r.rr_ratio}` },
        { label: 'Max SL',        key: 'max_sl_points', fmt: r => r.max_sl_points ? `${r.max_sl_points} pts` : 'off' },
        { label: 'SL Hunt',       key: 'require_sl_hunt', fmt: r => r.require_sl_hunt ? 'on' : 'off' },
        { label: 'Cut-off',       key: 'entry_cutoff',  fmt: r => r.entry_cutoff },
        { label: 'Trades',        key: 'total_trades',  fmt: r => r.total_trades },
        { label: 'Win%',          key: 'win_rate',      fmt: r => `${r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0'}%` },
        { label: 'Net P&L (pts)', key: 'total_pnl',     fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1)} pts</span>` },
        { label: 'Net P&L (₹)',   key: 'net_pnl_inr',   fmt: r => { const { lots, lotValue } = _spOptMoney(); const v = _spOptNetRs(r, lots, lotValue); return `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN')}</span>`; } },
        { label: 'Brokerage (₹)', key: 'brokerage_inr', fmt: r => { const { lots } = _spOptMoney(); const b = _spOptBrokerage(r, lots); return `<span class="pnl-negative">-₹${Math.round(b).toLocaleString('en-IN')}</span>`; } },
        { label: 'Prof. Factor',  key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD',        key: 'max_drawdown',  fmt: r => `<span class="pnl-negative">${r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—'}</span>` },
        { label: '',              key: null,            fmt: () => '' },   // Use button
    ];

    let _spOptGroupsByTf = {};

    function renderSpOptResults(data) {
        const panel     = document.getElementById('scalpPullbackOptimisePanel');
        const container = document.getElementById('spOptGrids');
        const metaEl    = document.getElementById('spOptMeta');
        const recalcBtn = document.getElementById('spRecalcOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _renderOptTfGrids(container, data.timeframes || [], SP_OPT_COLS, {
            idPrefix: 'spOptGrid',
            isLive: () => false,
            applyFn: applySpOptResult,
            stateStore: _spOptGroupsByTf,
            derivedSort: {
                win_rate: r => r.total_trades ? r.wins / r.total_trades : 0,
                net_pnl_inr: r => { const { lots, lotValue } = _spOptMoney(); return _spOptNetRs(r, lots, lotValue); },
                brokerage_inr: r => { const { lots } = _spOptMoney(); return _spOptBrokerage(r, lots); },
            },
        });

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applySpOptResult(data.best);
    }

    function cancelSpOptimise() {
        _spOptRun += 1;
        if (_spOptAbort) {
            try { _spOptAbort.abort(); } catch (e) { /* noop */ }
            _spOptAbort = null;
        }
        _hideOptLoader();
    }

    async function runSpOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('scalpPullbackOptimisePanel');
        const recalcBtn = document.getElementById('spRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        cancelSpOptimise();
        const myRun      = _spOptRun;
        const controller = new AbortController();
        _spOptAbort      = controller;
        _showOptLoader('Finding best params…', 'Sweeping pullback combos across timeframes…');

        try {
            const resp = await fetch('/api/backtest/scalp-pullback/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate').value,
                    end_date:    document.getElementById('endDate').value,
                    // Not part of the swept grid, so the sweep has to score
                    // under the same values the single run uses. Max SL is
                    // NOT sent — the grid sweeps it, since the right stop
                    // width depends on the index's own scale.
                    exit_cutoff:  document.getElementById('spExitCutoff')?.value || '15:15',
                    max_trades_per_day: parseInt(document.getElementById('spMaxTrades')?.value || 3),
                    trail_points: parseFloat(document.getElementById('spTrailSl')?.value) || null,
                    recalculate: recalculate,
                })
            });
            const data = await resp.json();
            if (myRun !== _spOptRun) return; // superseded
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            if (data.from_cache) {
                _spOptAbort = null;
                _hideOptLoader();
                renderSpOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            _pollSpOptimise(data.task_id, activeBtn, origText, Date.now(), myRun);
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            console.error('Scalp Pullback optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
            _hideOptLoader();
        }
    }

    function _pollSpOptimise(taskId, activeBtn, origText, startMs, myRun) {
        const MAX_WAIT_MS = 10 * 60 * 1000;
        function tick() {
            if (myRun !== _spOptRun) return;
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;
            _showOptLoader('Finding best params…', `${elapsed}s elapsed`);
            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            fetch(`/api/backtest/scalp-pullback/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _spOptRun) return;
                    if (data.status === 'running') { setTimeout(tick, 2000); return; }
                    _spOptAbort = null;
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    _hideOptLoader();
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    renderSpOptResults(data);
                })
                .catch(err => {
                    if (myRun !== _spOptRun) return;
                    console.error('Scalp Pullback poll error:', err);
                    setTimeout(tick, 3000);
                });
        }
        setTimeout(tick, 2000);
    }

    function applySpOptResult(r) {
        const touch = document.getElementById('spTouchLevel');
        const rr    = document.getElementById('spRrRatio');
        const dir   = document.getElementById('spDirection');
        const hunt  = document.getElementById('spRequireSlHunt');
        const cut   = document.getElementById('spEntryCutoff');
        const maxSl = document.getElementById('spMaxSl');
        const intervalSel = document.getElementById('interval');
        if (touch) touch.value = r.touch_level;
        if (rr)    rr.value    = r.rr_ratio;
        if (dir)   dir.value   = r.direction;
        if (hunt)  hunt.checked = !!r.require_sl_hunt;
        if (cut && r.entry_cutoff) cut.value = r.entry_cutoff;
        if (maxSl && r.max_sl_points != null) maxSl.value = r.max_sl_points;
        // Winning timeframe → main interval dropdown, so a follow-up single
        // backtest reproduces the optimised run.
        if (intervalSel && r.interval) intervalSel.value = r.interval;
        if (window.showNotification) {
            const tfStr = r.tf_label ? `${r.tf_label} · ` : '';
            window.showNotification(
                `Applied: ${tfStr}${r.touch_level}  ·  1:${r.rr_ratio}  ·  ${r.direction}  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    const spRecalcBtn = document.getElementById('spRecalcOptBtn');
    if (spRecalcBtn) spRecalcBtn.addEventListener('click', () => runSpOptimise(true));

    // ── Pivot Confluence Optimise (Find Best Params) — one grid per timeframe ─
    // Same shape as the Scalp Pullback sweep above. No Live column: this
    // strategy is backtest-only, with no live algo to badge rows against.
    function _pcOptMoney() {
        const lots     = Math.max(1, parseInt(document.getElementById('pcLots')?.value    || 1));
        const lotValue = Math.max(1, parseFloat(document.getElementById('pcLotValue')?.value || 65));
        return { lots, lotValue };
    }
    function _pcOptBrokerage(r, lots) { return calcBrokeragePerTrade(lots) * (r.total_trades || 0); }
    function _pcOptNetRs(r, lots, lotValue) { return (r.total_pnl || 0) * lotValue * lots - _pcOptBrokerage(r, lots); }

    const PC_TRIGGER_LABELS = { either: 'Either', yhl: 'YH/YL', pivot: 'Pivot' };
    const PC_CONF_LABELS    = { vwap_ema: 'VWAP+EMA', vwap: 'VWAP', ema: '20 EMA', none: 'none' };
    const PC_WINDOW_LABELS  = { both: 'AM + PM', morning: 'AM only', afternoon: 'PM only', all_day: 'All day' };

    const PC_OPT_COLS = [
        { label: '#',             key: null,             fmt: (r, i) => i + 1 },
        { label: 'Trigger',       key: 'entry_trigger',  fmt: r => PC_TRIGGER_LABELS[r.entry_trigger] || r.entry_trigger },
        { label: 'Confluence',    key: 'confluence',     fmt: r => PC_CONF_LABELS[r.confluence] || r.confluence },
        { label: 'Dir',           key: 'direction',      fmt: r => `<span style="white-space:nowrap">${r.direction}</span>` },
        { label: 'Target',        key: 'target_mode',    fmt: r => r.target_mode === 'next_level' ? 'Next level' : `1:${r.rr_ratio}` },
        { label: 'Window',        key: 'entry_windows',  fmt: r => PC_WINDOW_LABELS[r.entry_windows] || r.entry_windows },
        { label: 'AM Exit',       key: 'morning_exit',   fmt: r => r.morning_exit },
        { label: 'Trades',        key: 'total_trades',   fmt: r => r.total_trades },
        { label: 'Win%',          key: 'win_rate',       fmt: r => `${r.total_trades > 0 ? ((r.wins / r.total_trades) * 100).toFixed(0) : '0'}%` },
        { label: 'Net P&L (pts)', key: 'total_pnl',      fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_pnl >= 0 ? '+' : '') + r.total_pnl.toFixed(1)} pts</span>` },
        { label: 'Net P&L (₹)',   key: 'net_pnl_inr',    fmt: r => { const { lots, lotValue } = _pcOptMoney(); const v = _pcOptNetRs(r, lots, lotValue); return `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN')}</span>`; } },
        { label: 'Brokerage (₹)', key: 'brokerage_inr',  fmt: r => { const { lots } = _pcOptMoney(); const b = _pcOptBrokerage(r, lots); return `<span class="pnl-negative">-₹${Math.round(b).toLocaleString('en-IN')}</span>`; } },
        { label: 'Prof. Factor',  key: 'profit_factor',  fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD',        key: 'max_drawdown',   fmt: r => `<span class="pnl-negative">${r.max_drawdown != null ? r.max_drawdown.toFixed(1) : '—'}</span>` },
        { label: '',              key: null,             fmt: () => '' },   // Use button
    ];

    let _pcOptGroupsByTf = {};

    function renderPcOptResults(data) {
        const panel     = document.getElementById('pivotConfluenceOptimisePanel');
        const container = document.getElementById('pcOptGrids');
        const metaEl    = document.getElementById('pcOptMeta');
        const recalcBtn = document.getElementById('pcRecalcOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.symbol} · ${data.interval}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _renderOptTfGrids(container, data.timeframes || [], PC_OPT_COLS, {
            idPrefix: 'pcOptGrid',
            isLive: () => false,
            applyFn: applyPcOptResult,
            stateStore: _pcOptGroupsByTf,
            derivedSort: {
                win_rate: r => r.total_trades ? r.wins / r.total_trades : 0,
                net_pnl_inr: r => { const { lots, lotValue } = _pcOptMoney(); return _pcOptNetRs(r, lots, lotValue); },
                brokerage_inr: r => { const { lots } = _pcOptMoney(); return _pcOptBrokerage(r, lots); },
            },
        });

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyPcOptResult(data.best);
    }

    function cancelPcOptimise() {
        _pcOptRun += 1;
        if (_pcOptAbort) {
            try { _pcOptAbort.abort(); } catch (e) { /* noop */ }
            _pcOptAbort = null;
        }
        _hideOptLoader();
    }

    async function runPcOptimise(recalculate) {
        const symbol = symbolSearch.value.trim().toUpperCase();
        if (!symbol) { window.showNotification('Please select a symbol', 'warning'); return; }

        const panel     = document.getElementById('pivotConfluenceOptimisePanel');
        const recalcBtn = document.getElementById('pcRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        cancelPcOptimise();
        const myRun      = _pcOptRun;
        const controller = new AbortController();
        _pcOptAbort      = controller;
        _showOptLoader('Finding best params…', 'Sweeping pivot combos across timeframes…');

        try {
            const resp = await fetch('/api/backtest/pivot-confluence/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    symbol,
                    start_date:  document.getElementById('startDate').value,
                    end_date:    document.getElementById('endDate').value,
                    // Not part of the swept grid, so the sweep has to score
                    // under the same values the single run uses.
                    exit_cutoff:  document.getElementById('pcExitCutoff')?.value || '15:15',
                    max_trades_per_day: parseInt(document.getElementById('pcMaxTrades')?.value || 2),
                    max_sl_points: parseFloat(document.getElementById('pcMaxSl')?.value) || 0,
                    trail_points: parseFloat(document.getElementById('pcTrailSl')?.value) || null,
                    recalculate: recalculate,
                })
            });
            const data = await resp.json();
            if (myRun !== _pcOptRun) return; // superseded
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            if (data.from_cache) {
                _pcOptAbort = null;
                _hideOptLoader();
                renderPcOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            _pollPcOptimise(data.task_id, activeBtn, origText, Date.now(), myRun);
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            console.error('Pivot Confluence optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
            _hideOptLoader();
        }
    }

    function _pollPcOptimise(taskId, activeBtn, origText, startMs, myRun) {
        const MAX_WAIT_MS = 10 * 60 * 1000;
        function tick() {
            if (myRun !== _pcOptRun) return;
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;
            _showOptLoader('Finding best params…', `${elapsed}s elapsed`);
            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            fetch(`/api/backtest/pivot-confluence/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _pcOptRun) return;
                    if (data.status === 'running') { setTimeout(tick, 2000); return; }
                    _pcOptAbort = null;
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    _hideOptLoader();
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    renderPcOptResults(data);
                })
                .catch(err => {
                    if (myRun !== _pcOptRun) return;
                    console.error('Pivot Confluence poll error:', err);
                    setTimeout(tick, 3000);
                });
        }
        setTimeout(tick, 2000);
    }

    function applyPcOptResult(r) {
        const trig   = document.getElementById('pcTrigger');
        const conf   = document.getElementById('pcConfluence');
        const dir    = document.getElementById('pcDirection');
        const tmode  = document.getElementById('pcTargetMode');
        const rr     = document.getElementById('pcRrRatio');
        const win    = document.getElementById('pcWindows');
        const amExit = document.getElementById('pcMorningExit');
        const intervalSel = document.getElementById('interval');
        if (trig)  trig.value  = r.entry_trigger;
        if (conf)  conf.value  = r.confluence;
        if (dir)   dir.value   = r.direction;
        if (tmode) tmode.value = r.target_mode;
        if (rr)    rr.value    = r.rr_ratio;
        if (win)   win.value   = r.entry_windows;
        // 'off' means the morning force-close was disabled — clearing the time
        // input is how the form expresses that.
        if (amExit) amExit.value = (r.morning_exit && r.morning_exit !== 'off') ? r.morning_exit : '';
        // Winning timeframe → main interval dropdown, so a follow-up single
        // backtest reproduces the optimised run.
        if (intervalSel && r.interval) intervalSel.value = r.interval;
        if (window.showNotification) {
            const tfStr = r.tf_label ? `${r.tf_label} · ` : '';
            window.showNotification(
                `Applied: ${tfStr}${PC_TRIGGER_LABELS[r.entry_trigger] || r.entry_trigger}  ·  ${PC_CONF_LABELS[r.confluence] || r.confluence}  ·  ${r.direction}  ·  Win% ${((r.wins / r.total_trades) * 100).toFixed(0)}%`, 'success'
            );
        }
    }

    const pcRecalcBtn = document.getElementById('pcRecalcOptBtn');
    if (pcRecalcBtn) pcRecalcBtn.addEventListener('click', () => runPcOptimise(true));

    // ── 30-Min Opening Fakeout Optimise ──────────────────────────────────
    // Sweeps Direction × SL/Target-Confirm × the 3 boolean pattern filters
    // (48 combos) across the full F&O stock universe — no per-symbol/
    // per-timeframe axis like 2nd-Candle, since this strategy always
    // scans every stock at a fixed 30-min-candle resolution. One flat
    // leaderboard, ranked by real ₹ Net P&L.
    const TMF_OPT_COLS = [
        { label: '#',              key: null,                  fmt: (r, i) => i + 1 },
        { label: 'Direction',      key: 'direction',           fmt: r => r.direction },
        { label: 'Entry Buffer',   key: 'use_entry_buffer',     fmt: r => r.use_entry_buffer ? 'On' : 'Off' },
        { label: 'Body Filter',    key: 'use_body_filter',      fmt: r => r.use_body_filter ? 'On' : 'Off' },
        { label: 'C2 Close',       key: 'use_c2_close_filter',  fmt: r => r.use_c2_close_filter ? 'On' : 'Off' },
        { label: 'Trades',         key: 'total_trades',         fmt: r => r.total_trades },
        { label: 'Win%',           key: 'win_rate',             fmt: r => `${r.win_rate ?? 0}%` },
        { label: 'Net P&L (₹)',    key: 'net_pnl_rupees',       fmt: r => `<span class="${r.net_pnl_rupees >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.net_pnl_rupees >= 0 ? '+' : '') + '₹' + Math.round(r.net_pnl_rupees).toLocaleString('en-IN')}</span>` },
        { label: 'Prof. Factor',   key: 'profit_factor_rupees', fmt: r => (r.profit_factor_rupees || 0).toFixed(2) },
        { label: 'Max DD (₹)',     key: 'max_drawdown_rupees',  fmt: r => `<span class="pnl-negative">₹${Math.round(r.max_drawdown_rupees || 0).toLocaleString('en-IN')}</span>` },
        { label: '',               key: null,                  fmt: () => '' },   // Use button
    ];

    function renderTmfOptResults(data) {
        const panel     = document.getElementById('thirtyMinFakeoutOptimisePanel');
        const metaEl    = document.getElementById('tmfOptMeta');
        const recalcBtn = document.getElementById('tmfRecalcOptBtn');

        if (metaEl) {
            let meta = `${data.total_combos_tested} combos · ${data.total} passed min-trades · ${data.symbols_used} stocks`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        _mountSingleOptGrid('tmfOptGrid', data.results || [], TMF_OPT_COLS, 'net_pnl_rupees', applyTmfOptResult,
            { win_rate: r => r.total_trades ? r.wins / r.total_trades : 0 });

        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyTmfOptResult(data.best);
    }

    function cancelTmfOptimise() {
        _tmfOptRun += 1;
        if (_tmfOptAbort) {
            try { _tmfOptAbort.abort(); } catch (e) { /* noop */ }
            _tmfOptAbort = null;
        }
        _hideOptLoader();
    }

    // Progress card — shared by every "Find Best Params" sweep. Reuses the
    // #loading card (already sits right above #resultsArea in the DOM)
    // instead of cramming elapsed time/combo progress into the button text.
    // It is what a Recalculate run has for feedback at all: the Recalculate
    // buttons live INSIDE their results panel, which each run*Optimise()
    // hides while it works, so their own ⏳ label goes off-screen with it.
    // #resultsArea is deliberately left alone (not hidden) so a previous
    // run's stat cards stay visible with the loader stacked above them.
    function _showOptLoader(title, sub) {
        const loading    = document.getElementById('loading');
        const titleEl    = document.getElementById('btLoaderTitle');
        const subEl      = document.getElementById('btLoaderSub');
        const placeholder = document.getElementById('btRightPlaceholder');
        if (titleEl) titleEl.innerHTML = `${title}<span class="bt-dots"></span>`;
        if (subEl)   subEl.textContent = sub;
        if (loading) loading.style.display = 'flex';
        if (placeholder) placeholder.style.display = 'none';
        _optLoaderActive = true;
    }
    function _hideOptLoader() {
        // No-op unless an optimise run owns the card — a plain backtest drives
        // the same #loading element and must not have it pulled out from under
        // it by a cancel*Optimise() on some unrelated symbol/strategy change.
        if (!_optLoaderActive) return;
        _optLoaderActive = false;
        const loading = document.getElementById('loading');
        const titleEl = document.getElementById('btLoaderTitle');
        const subEl   = document.getElementById('btLoaderSub');
        if (loading) loading.style.display = 'none';
        // Restore the default text so a later plain "BACKTEST" run doesn't
        // show stale optimiser progress.
        if (titleEl) titleEl.innerHTML = 'Running backtest<span class="bt-dots"></span>';
        if (subEl)   subEl.textContent = 'Fetching candles & scanning signals';
    }

    async function runTmfOptimise(recalculate) {
        const panel     = document.getElementById('thirtyMinFakeoutOptimisePanel');
        const recalcBtn = document.getElementById('tmfRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        // Button just shows a simple, static loading state — the live
        // elapsed-time/progress count goes on the loader card instead.
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';

        cancelTmfOptimise();   // cancel any prior in-flight run (also clears its loader)
        _showOptLoader('Finding best params…', 'Fetching data for the full universe…');
        const myRun      = _tmfOptRun;
        const controller = new AbortController();
        _tmfOptAbort     = controller;

        const exitTime = (document.getElementById('tmfExitTime')?.value || '15:18').split(':');
        try {
            const resp = await fetch('/api/backtest/thirty-min-fakeout/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    symbol:             document.getElementById('tmfSymbol')?.value || '',
                    start_date:         document.getElementById('startDate').value,
                    end_date:           document.getElementById('endDate').value,
                    exit_hour:          parseInt(exitTime[0] || 15),
                    exit_minute:        parseInt(exitTime[1] || 18),
                    use_sl_risk_filter: document.getElementById('tmfUseSlRiskFilter')?.checked ?? true,
                    sl_risk_max:        parseFloat(document.getElementById('tmfSlRiskMax')?.value || '5000') || 5000,
                    recalculate,
                })
            });
            const data = await resp.json();
            if (myRun !== _tmfOptRun) return; // superseded
            if (!data.success) {
                window.showNotification(data.error || 'Optimisation failed', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            if (data.from_cache) {
                _tmfOptAbort = null;
                _hideOptLoader();
                renderTmfOptResults(data);
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                return;
            }
            _pollTmfOptimise(data.task_id, activeBtn, origText, Date.now(), myRun);
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            console.error('30-Min Fakeout optimise error:', err);
            window.showNotification('Optimisation request failed', 'error');
            if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
            _hideOptLoader();
        }
    }

    function _pollTmfOptimise(taskId, activeBtn, origText, startMs, myRun) {
        const MAX_WAIT_MS = 20 * 60 * 1000;   // full-universe x 48 combos — generous
        function tick() {
            if (myRun !== _tmfOptRun) return;
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
                return;
            }
            fetch(`/api/backtest/thirty-min-fakeout/optimise/status/${taskId}`)
                .then(r => r.json())
                .then(data => {
                    if (myRun !== _tmfOptRun) return;
                    if (data.status === 'running') {
                        _showOptLoader('Finding best params…', `${elapsed}s elapsed · ${data.progress || 'starting…'}`);
                        setTimeout(tick, 2000);
                        return;
                    }
                    _tmfOptAbort = null;
                    if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                    _hideOptLoader();
                    if (!data.success || data.status === 'error') {
                        window.showNotification(data.error || 'Optimisation failed', 'error');
                        return;
                    }
                    renderTmfOptResults(data);
                })
                .catch(err => {
                    if (myRun !== _tmfOptRun) return;
                    console.error('30-Min Fakeout poll error:', err);
                    setTimeout(tick, 3000);
                });
        }
        setTimeout(tick, 2000);
    }

    // "Use" only populates the form fields with the row's combo — it never
    // triggers a backtest run itself. The user runs the backtest manually
    // once they're happy with the populated inputs.
    function applyTmfOptResult(r) {
        const dir = document.getElementById('tmfDirection');
        const entryBuf = document.getElementById('tmfUseEntryBuffer');
        const bodyFilt = document.getElementById('tmfUseBodyFilter');
        const c2Filt   = document.getElementById('tmfUseC2CloseFilter');
        if (dir) dir.value = r.direction;
        if (entryBuf) entryBuf.checked = !!r.use_entry_buffer;
        if (bodyFilt) bodyFilt.checked = !!r.use_body_filter;
        if (c2Filt) c2Filt.checked = !!r.use_c2_close_filter;
        if (window.showNotification) {
            window.showNotification(
                `Applied: ${r.direction}  ·  Win% ${r.win_rate}%  ·  Net ₹${Math.round(r.net_pnl_rupees).toLocaleString('en-IN')}`,
                'success'
            );
        }
    }

    const tmfRecalcBtn = document.getElementById('tmfRecalcOptBtn');
    if (tmfRecalcBtn) tmfRecalcBtn.addEventListener('click', () => runTmfOptimise(true));

    const optimiseBtn   = document.getElementById('runOptimiseBtn');
    if (optimiseBtn)  optimiseBtn.addEventListener('click', () => {
        const strat = strategySelect ? strategySelect.value : '';
        if (strat === 'swing_momentum') _runSmOptimise(false);
        else if (strat === 'second_candle') runScOptimise(false);
        else if (strat === 'option_breakout') runObOptimise(false);
        else if (strat === 'thirty_min_fakeout') runTmfOptimise(false);
        else if (strat === 'ema_pullback') runEmaOptimise(false);
        else if (strat === 'scalp_pullback') runSpOptimise(false);
        else if (strat === 'pivot_confluence') runPcOptimise(false);
        else if (strat === 'weekly_cpr_breakout') runWcbOptimise(false);
        else if (strat === 'option_selling') runOsOptimise(false);
        else if (strat === 'simpler_trader') runSbrOptimise(true);
        else if (SIMPLE_STRATS.includes(strat)) runSbrOptimise(false, strat);
    });

    // ── Simple Trader — Find Best Params ────────────────────────────────
    let _sbrOptRun = 0;

    // Simple Trader board: the page's own filters stay as set; the numbers Quantman's
    // run picked by eye (RSI line, SuperTrend, BB, %R level, entry start) are swept.
    const SBR_SIMPLE_COLS = [
        { label: '#',          key: null,            fmt: (r, i) => r._current ? '★ Default' : i + 1 },
        { label: 'RSI >',      key: 'rsi_level',     fmt: r => r.rsi_level },
        { label: 'SuperTrend', key: 'st_mult',       fmt: r => `${r.st_period} / ${r.st_mult}` },
        { label: 'BB',         key: 'bb_mult',       fmt: r => `${r.bb_period} / ${r.bb_mult}` },
        { label: '%R >',       key: 'wr_level',      fmt: r => r.wr_level },
        { label: 'From',       key: 'entry_start',   fmt: r => r.entry_start },
        { label: 'Trades',     key: 'total_trades',  fmt: r => r.total_trades + (r.option ? ` <small>(option ${r.option.total_trades})</small>` : '') },
        { label: 'Win%',       key: 'win_rate',      fmt: r => `${(r.win_rate ?? 0).toFixed(0)}%` + (r.option ? ` <small>(option ${r.option.win_rate.toFixed(0)}%)</small>` : '') },
        { label: 'Total (pts)', key: 'total_pnl', fmt: r => `<span class="${r.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${r.total_pnl >= 0 ? '+' : ''}${(r.total_pnl ?? 0).toFixed(0)}</span>` },
        { label: 'Recent 30% (pts)', key: 'recent_pnl', fmt: r => `<span class="${r.recent_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${r.recent_pnl >= 0 ? '+' : ''}${(r.recent_pnl ?? 0).toFixed(0)}</span>` },
        { label: 'Prof. Factor', key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) + (r.option ? ` <b title="On the option with your SL / TSL — the figure the BACKTEST shows">(option ${r.option.profit_factor.toFixed(2)})</b>` : '') },
        { label: 'Index PF', key: 'index_pf', fmt: r => r.index_pf != null ? r.index_pf.toFixed(2) : '' },
        { label: 'Max DD (pts)', key: 'max_drawdown', fmt: r => `<span class="pnl-negative">${Math.round(Math.abs(r.max_drawdown || 0)).toLocaleString('en-IN')}</span>` },
        { label: '',           key: null,            fmt: () => '' },   // Use button
    ];

    function applySbrSimpleResult(r) {
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        set('sbrRsi', r.rsi_level);
        set('sbrStLen', r.st_period);
        set('sbrStFactor', r.st_mult);
        set('sbrBbLen', r.bb_period);
        set('sbrBbMult', r.bb_mult);
        set('sbrWrLevel', r.wr_level);
        set('sbrEntryStart', r.entry_start);
        if (window.showNotification) window.showNotification(
            `Applied: RSI > ${r.rsi_level} · ST ${r.st_period}/${r.st_mult} · BB ${r.bb_period}/${r.bb_mult}`
            + ` · %R > ${r.wr_level} · from ${r.entry_start} — press BACKTEST to see it on the option`, 'success');
    }

    // Strategies 2-7: the swept names come back with the rows, so the board and the
    // "use this row" write-back are built from them. Input ids are <prefix><suffix>.
    const SBR_PARAM = {
        st_period:  ['ST len',     'StLen'],     st_mult:   ['ST ×',       'StFactor'],
        bb_period:  ['BB len',     'BbLen'],     bb_mult:   ['BB ×',       'BbMult'],
        rsi_level:  ['RSI >',      'RsiLevel'],  rsi_entry: ['RSI entry',  'RsiEntry'],
        rsi_exit:   ['RSI exit',   'RsiExit'],   wr_level:  ['%R',         'WrLevel'],
        cci_level:  ['CCI',        'CciLevel'],  cpr_wide:  ['CPR wide <', 'CprWide'],
        cpr_narrow: ['CPR narrow <', 'CprNarrow'],
    };
    const SBR_PREFIX = { st_ema_rsi_cci: 'sec', st_rsi_pp_bb: 'srp', st_rsi_pp_bb_fut: 'srf',
                         st_rsi_cci_wr_pp_bb_fut: 'scw', st_rsi_cpr_bb_sx: 'scx', st_rsi_cpr_bb_wr_sx: 'swx' };

    function sbrStrategyCols(swept) {
        const pnl = (v) => `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${v >= 0 ? '+' : ''}${(v ?? 0).toFixed(0)}</span>`;
        return [
            { label: '#', key: null, fmt: (r, i) => r._current ? '★ Default' : i + 1 },
            ...swept.map(k => ({ label: (SBR_PARAM[k] || [k])[0], key: k, fmt: r => r[k] })),
            { label: 'Trades', key: 'total_trades', fmt: r => r.total_trades },
            { label: 'Win%', key: 'win_rate', fmt: r => `${(r.win_rate ?? 0).toFixed(0)}%` },
            { label: 'Total (pts)', key: 'total_pnl', fmt: r => pnl(r.total_pnl) },
            { label: 'Recent 30% (pts)', key: 'recent_pnl', fmt: r => pnl(r.recent_pnl) },
            { label: 'Prof. Factor', key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) },
            { label: 'Index PF', key: 'index_pf', fmt: r => r.index_pf != null ? r.index_pf.toFixed(2) : '' },
            { label: 'Max DD (pts)', key: 'max_drawdown', fmt: r => `<span class="pnl-negative">${Math.round(Math.abs(r.max_drawdown || 0)).toLocaleString('en-IN')}</span>` },
            { label: '', key: null, fmt: () => '' },   // Use button
        ];
    }

    function applySbrStrategyResult(strategy, swept) {
        return (r) => {
            const pre = SBR_PREFIX[strategy];
            const parts = [];
            swept.forEach(k => {
                const el = document.getElementById(pre + (SBR_PARAM[k] || [])[1]);
                if (el) el.value = r[k];
                parts.push(`${(SBR_PARAM[k] || [k])[0]} ${r[k]}`);
            });
            if (window.showNotification) window.showNotification(
                `Applied: ${parts.join(' · ')} — press BACKTEST to see it on the option`, 'success');
        };
    }

    function renderSbrOptResults(data) {
        const panel  = document.getElementById('stBbRsiOptimisePanel');
        const metaEl = document.getElementById('sbrOptMeta');
        if (metaEl) metaEl.textContent =
            `${data.total_combos_tested} combos · ${data.qualified} with ≥ ${data.min_trades} trades`
            + (data.passed != null ? ` · ${data.passed} also win ≥ ${data.min_win}% on ≥ 150 trades with a recent profit` : '') + ` · `
            + `${data.symbol} ${data.interval} · ${data.start_date} → ${data.end_date} · `
            + (data.basis === 'option'
                ? `the best index rows were re-priced on the option with your SL / TSL (${data.verified} checked) — these are the numbers BACKTEST shows; ranked on profit factor, then lowest drawdown, `
                : (data.mode === 'simple' || data.mode === 'strategy'
                    ? 'ranked on profit factor, then lowest drawdown, on index points, ' : 'ranked on index points, '))
            + `"recent" = since ${data.recent_from}`;
        const simple = data.mode === 'simple';
        const strat  = data.mode === 'strategy';
        // Option-priced boards carry the results cards' own figures (same books as the run).
        const rsCols = data.basis !== 'option' ? [] : [
            { label: 'Win / Loss', key: 'wins', fmt: r => `${r.wins} / ${r.losses}` },
            { label: 'Total P&L (₹)', key: 'gross_rs', fmt: r => _rs(r.gross_rs || 0) },
            { label: 'Net P&L (₹)', key: 'net_rs', fmt: r => _rs(r.net_rs || 0) },
            { label: 'Avg Win (₹)', key: 'avg_win_rs', fmt: r => _rs(r.avg_win_rs || 0) },
            { label: 'Avg Loss (₹)', key: 'avg_loss_rs', fmt: r => _rs(r.avg_loss_rs || 0) },
            // As the card reads: rupees first, the same drawdown in premium points beside it.
            { label: 'Max DD', key: 'max_dd_rs', fmt: r => `<span class="pnl-negative">₹${Math.round(Math.abs(r.max_dd_rs || 0)).toLocaleString('en-IN')}</span> <small>(${Math.abs(r.max_drawdown || 0).toFixed(1)} pts)</small>` },
        ];
        const withRs = (cols) => cols.length && rsCols.length
            ? [...cols.filter(c => c.label !== 'Max DD (pts)').slice(0, -1), ...rsCols, cols[cols.length - 1]] : cols;
        // The page's own settings, scored the same way, pinned as row ★ so the board can be
        // read against the run the user already trusts (the sweep has no SL/TSL, see note).
        const cur = data.current;
        let rows = data.results || [];
        if (cur) {
            // The default (current page settings) is one row on the board: if the sweep already
            // holds that exact combination, flag it; otherwise add it, scored the same way.
            const keys = ['rsi_level', 'st_period', 'st_mult', 'bb_period', 'bb_mult', 'wr_level',
                          'entry_start', 'cci_level', 'cpr_wide', 'cpr_narrow', 'rsi_entry', 'rsi_exit'];
            const same = (r) => keys.every(k => r[k] === undefined || cur[k] === undefined || String(r[k]) === String(cur[k]));
            const hit = rows.findIndex(same);
            if (hit >= 0) rows = rows.map((r, i) => i === hit ? { ...r, _current: true, rank: cur.rank, option: r.option } : r);
            else rows = [{ ...cur, _current: true }, ...rows];
        }
        if (metaEl && cur) metaEl.textContent += ` · ★ Default (current settings): ${cur.total_trades} trades, `
            + `${cur.win_rate.toFixed(1)}% win, PF ${(cur.profit_factor || 0).toFixed(2)}, `
            + `${cur.total_pnl >= 0 ? '+' : ''}${(cur.total_pnl ?? 0).toFixed(0)} pts `
            + (data.basis === 'option' ? '(on the option) ' : '(index points) ')
            + (cur.rank ? `— #${cur.rank} of ${data.basis === 'option' ? data.verified : (data.passed ?? data.qualified)}`
                        : '— outside the quality filter (win < 50%, under 150 trades or no recent profit)')
            + (data.option_error ? `. Option pricing unavailable (${data.option_error}); showing index points.` : '');
        _mountSingleOptGrid('sbrOptGrid', rows,
                            withRs(strat ? sbrStrategyCols(data.swept || []) : SBR_SIMPLE_COLS),
                            (simple || strat) ? 'profit_factor' : 'total_pnl',
                            strat ? applySbrStrategyResult(data.strategy, data.swept || [])
                                  : applySbrSimpleResult);
        if (panel) panel.style.display = '';
    }

    async function runSbrOptimise(simple, strategy) {
        const optimBtn = document.getElementById('runOptimiseBtn');
        const origText = optimBtn ? optimBtn.textContent : '';
        const panel    = document.getElementById('stBbRsiOptimisePanel');
        if (optimBtn) { optimBtn.textContent = '⏳ Running…'; optimBtn.disabled = true; }
        if (panel) panel.style.display = 'none';
        const myRun = ++_sbrOptRun;
        const done = () => { if (optimBtn) { optimBtn.textContent = origText; optimBtn.disabled = false; } };
        _showOptLoader('Finding best params…', 'Starting…');
        const num = (id, d) => parseFloat(document.getElementById(id)?.value || d);
        try {
            let stratBody = null;
            if (strategy) {
                // The strategy's own run builds its payload; take it instead of posting it.
                let cap = null;
                window._btCapture = (c) => { cap = c; };
                runBtn.click();
                window._btCapture = null;
                if (!cap) throw new Error('Could not read this strategy\'s settings');
                stratBody = { ...cap.payload, strategy };
                _showOptLoader('Finding best params…', 'Starting…');   // the run's own start hid it
            }
            const resp = await fetch('/api/backtest/st-bb-rsi/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(stratBody || {
                    symbol:     (symbolSearch.value || 'NIFTY').trim().toUpperCase(),
                    start_date: document.getElementById('startDate')?.value,
                    end_date:   document.getElementById('endDate')?.value,
                    interval:   document.getElementById('interval')?.value || '3minute',
                    rsi_period: parseInt(num('sbrRsiLen', 14)),
                    bb_period:  parseInt(num('sbrBbLen', 20)),
                    bb_offset:  parseInt(num('sbrBbOffset', 0)),
                    entry_end:  document.getElementById('sbrEntryEnd')?.value || '15:14',
                    exit_cutoff: document.getElementById('sbrExitCutoff')?.value || '15:14',
                    max_trades_per_day: parseInt(num('sbrMaxTrades', 2)),
                    ...(simple ? {
                        mode: 'simple',
                        direction:     document.getElementById('sbrDirection')?.value || 'long',
                        htf:           document.getElementById('sbrHtf')?.value || 'off',
                        require_green: document.getElementById('sbrGreen')?.checked ?? false,
                        use_rsi:   document.getElementById('sbrUseRsi')?.checked ?? true,
                        use_bb:    document.getElementById('sbrUseBb')?.checked ?? true,
                        use_st:    document.getElementById('sbrUseSt')?.checked ?? true,
                        use_pivot: document.getElementById('sbrUsePivot')?.checked ?? true,
                        use_wr:    document.getElementById('sbrUseWr')?.checked ?? false,
                        wr_period: parseInt(num('sbrWrLen', 14)),
                        wr_level:  num('sbrWrLevel', -30),
                        // the page's own numbers, scored as the ★ current row
                        rsi_level: num('sbrRsi', 70),
                        st_period: parseInt(num('sbrStLen', 10)),
                        st_mult:   num('sbrStFactor', 2),
                        bb_mult:   num('sbrBbMult', 2),
                        entry_start: document.getElementById('sbrEntryStart')?.value || '09:15',
                        lots: Math.max(1, parseInt(num('pcLots', 1))), lot_value: num('pcLotValue', 65),
                        compounding: document.getElementById('sbrCompound')?.checked ?? false,
                        start_capital: num('sbrCapital', 25000), per_lot: num('sbrPerLot', 0),
                        premium:   num('sbrPremium', 250),
                        price_options: document.getElementById('sbrPriceOptions')?.checked ?? true,
                        sl_pct: num('sbrSlPct', 0), tsl_x: num('sbrTslX', 0), tsl_y: num('sbrTslY', 0),
                    } : {}),
                }),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Optimisation failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2000));
                if (myRun !== _sbrOptRun) return;
                data = await (await fetch(`/api/backtest/st-bb-rsi/optimise/status/${taskId}`)).json();
                if (myRun !== _sbrOptRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Finding best params…', p.stage === 'pricing'
                    ? `pricing the best on the option · ${p.done} / ${p.total} · ${data.elapsed || 0}s`
                    : (p.total ? `${p.done} / ${p.total} combos · ${data.elapsed || 0}s` : `reading candles · ${data.elapsed || 0}s`));
            }
            _hideOptLoader();
            done();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Optimisation failed');
            renderSbrOptResults(data);
        } catch (err) {
            if (myRun !== _sbrOptRun) return;
            _hideOptLoader();
            done();
            console.error('ST+BB+RSI optimise error:', err);
            window.showNotification(err.message || 'Optimisation failed', 'error');
        }
    }

    // ── NIFTY Option Selling — Find Best Params ─────────────────────────
    // Sweeps OTM strikes × stop % × re-entry × exit time (300 combos) over the
    // days / entry time / lots / dates the page has set. Premiums for ATM..±4
    // strikes are bought once per day; every combo replays them, so the cost is
    // the fetch (up to 10 ICICI requests a day cold), not the combos.
    let _osOptRun = 0;
    const OS_OPT_COLS = [
        { label: '#',            key: null,            fmt: (r, i) => i + 1 },
        { label: 'OTM Strikes',  key: 'otm_strikes',   fmt: r => r.otm_strikes === 0 ? 'ATM' : `${r.otm_strikes} (${r.otm_strikes * 50} pts)` },
        { label: 'Stop Loss',    key: 'sl_pct',        fmt: r => `${r.sl_pct}%` },
        { label: 'Re-entry',     key: 'reentry',       fmt: r => r.reentry ? 'Once, at cost' : 'Off' },
        { label: 'Exit',         key: 'exit_time',     fmt: r => r.exit_time },
        { label: 'Days',         key: 'total_trades',  fmt: r => r.total_trades },
        { label: 'Win%',         key: 'win_rate',      fmt: r => `${(r.win_rate ?? 0).toFixed(0)}%` },
        { label: 'Total P&L (gross)', key: 'gross_pnl_rupees', fmt: r => _rs(r.gross_pnl_rupees || 0) },
        { label: 'Net P&L (₹)',  key: 'net_pnl_rupees', fmt: r => _rs(r.net_pnl_rupees) },
        { label: 'Avg / Day',    key: 'avg_pnl_rupees', fmt: r => _rs(r.avg_pnl_rupees) },
        { label: 'Prof. Factor', key: 'profit_factor', fmt: r => (r.profit_factor || 0).toFixed(2) },
        { label: 'Max DD (₹)',   key: 'max_drawdown_rupees', fmt: r => `<span class="pnl-negative">₹${Math.round(Math.abs(r.max_drawdown_rupees || 0)).toLocaleString('en-IN')}</span>` },
        { label: 'Stops',        key: 'sl_hits',       fmt: r => r.sl_hits },
        { label: '',             key: null,            fmt: () => '' },   // Use button
    ];

    function applyOsOptResult(r) {
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        set('osOtm', String(r.otm_strikes));
        set('osSlPct', String(r.sl_pct));
        set('osReentry', r.reentry ? 'true' : 'false');
        set('osExitTime', r.exit_time);
        if (window.showNotification) window.showNotification(
            `Applied: ${r.otm_strikes === 0 ? 'ATM' : r.otm_strikes + ' OTM'} · SL ${r.sl_pct}% · `
            + `re-entry ${r.reentry ? 'on' : 'off'} · exit ${r.exit_time} · Win% ${(r.win_rate ?? 0).toFixed(0)}%`, 'success');
    }

    function renderOsOptResults(data) {
        const panel     = document.getElementById('optionSellingOptimisePanel');
        const metaEl    = document.getElementById('osOptMeta');
        const recalcBtn = document.getElementById('osRecalcOptBtn');
        if (metaEl) metaEl.textContent =
            `${data.total_combos_tested} combos · ${data.total} scored · ${data.days_priced}/${data.days_scanned} days priced (ICICI)`
            + (data.breeze_errors ? ` · ${data.breeze_errors} ICICI requests failed` : '');
        _mountSingleOptGrid('osOptGrid', data.results || [], OS_OPT_COLS, 'net_pnl_rupees', applyOsOptResult);
        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyOsOptResult(data.best);
    }

    async function runOsOptimise(recalculate) {
        const panel     = document.getElementById('optionSellingOptimisePanel');
        const recalcBtn = document.getElementById('osRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        const days = Array.from(document.querySelectorAll('.osDay:checked')).map(c => parseInt(c.value));
        if (!days.length) { window.showNotification('Pick at least one trading day', 'warning'); return; }
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';
        const osRes = document.getElementById('osResults');
        if (osRes) osRes.style.display = 'none';
        const myRun = ++_osOptRun;
        const done = () => { if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; } };
        _showOptLoader('Finding best params…', 'Starting…');
        try {
            const resp = await fetch('/api/backtest/option-selling/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    start_date: document.getElementById('startDate')?.value,
                    end_date:   document.getElementById('endDate')?.value,
                    days,
                    entry_time: document.getElementById('osEntryTime')?.value || '09:30',
                    lots:       Math.max(1, parseInt(document.getElementById('osLots')?.value || 1)),
                }),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Optimisation failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2500));
                if (myRun !== _osOptRun) return;
                data = await (await fetch(`/api/backtest/option-selling/optimise/status/${taskId}`)).json();
                if (myRun !== _osOptRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Finding best params…', p.stage === 'scoring'
                    ? `Scoring 300 combos · ${data.elapsed || 0}s`
                    : `${p.done || 0} / ${p.total || 0} days · ${p.traded || 0} priced (ICICI) · ${data.elapsed || 0}s`);
            }
            _hideOptLoader();
            done();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Optimisation failed');
            renderOsOptResults(data);
        } catch (err) {
            if (myRun !== _osOptRun) return;
            _hideOptLoader();
            done();
            console.error('Option Selling optimise error:', err);
            window.showNotification(err.message || 'Optimisation failed', 'error');
        }
    }
    const osRecalcBtn = document.getElementById('osRecalcOptBtn');
    if (osRecalcBtn) osRecalcBtn.addEventListener('click', () => runOsOptimise(true));

    // ── Weekly CPR Breakout — Find Best Params ──────────────────────────
    // Sweeps Target × W CPR Width × Min 1H Range × Direction (192 combos)
    // over the Symbol box's stock(s) and the Start/End dates, scored on the
    // FUTURES basis (sweeping option legs would be tens of thousands of ICICI
    // requests). Ranked by net ₹; "Last 3 yrs" is the end of the range on its
    // own — a combo whose total comes from old years shows up there.
    const _rs = v => `<span class="${v >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN')}</span>`;
    const WCB_OPT_COLS = [
        { label: '#',              key: null,                   fmt: (r, i) => i + 1 },
        { label: 'Target',         key: 'rr_ratio',             fmt: r => `1 : ${r.rr_ratio}` },
        { label: 'W CPR Width',    key: 'max_cpr_ratio',        fmt: r => r.max_cpr_ratio === 'any' ? 'Any' : `≤ ${r.max_cpr_ratio}×` },
        { label: 'Min 1H Range',   key: 'min_range_pct',        fmt: r => r.min_range_pct ? `${r.min_range_pct}%` : 'Off' },
        { label: 'Direction',      key: 'direction',            fmt: r => r.direction === 'both' ? 'Buy & Sell' : (r.direction === 'buy' ? 'Buy' : 'Sell') },
        { label: 'Trades',         key: 'total_trades',         fmt: r => r.total_trades },
        { label: 'Win%',           key: 'win_rate',             fmt: r => `${r.win_rate ?? 0}%` },
        // Named after the backtest cards they match: "Total P&L" there is
        // gross (before brokerage), "Net P&L (₹)" is after it.
        { label: 'Total P&L (gross)', key: 'gross_pnl_rupees',  fmt: r => _rs(r.gross_pnl_rupees || 0) },
        { label: 'Net P&L (₹)',    key: 'net_pnl_rupees',       fmt: r => _rs(r.net_pnl_rupees) },
        { label: 'Avg / Trade',    key: 'avg_pnl_rupees',       fmt: r => _rs(r.avg_pnl_rupees) },
        { label: 'Prof. Factor',   key: 'profit_factor_rupees', fmt: r => (r.profit_factor_rupees || 0).toFixed(2) },
        { label: 'Max DD (₹)',     key: 'max_drawdown_rupees',  fmt: r => `<span class="pnl-negative">₹${Math.round(Math.abs(r.max_drawdown_rupees || 0)).toLocaleString('en-IN')}</span>` },
        { label: 'Last 3 yrs',     key: 'recent_net_rupees',    fmt: r => r.recent_trades
              ? `${_rs(r.recent_net_rupees)} <small>(${r.recent_trades} · ${r.recent_win_rate}%)</small>` : '—' },
        { label: '',               key: null,                   fmt: () => '' },   // Use button
    ];

    function renderWcbOptResults(data) {
        const panel     = document.getElementById('weeklyCprBreakoutOptimisePanel');
        const metaEl    = document.getElementById('wcbOptMeta');
        const recalcBtn = document.getElementById('wcbRecalcOptBtn');
        const basisTxt = data.basis === 'option'
            ? 'Stock option ₹ (ATM, ICICI premiums) — same as the backtest'
            : (data.basis_note === 'all_stocks'
                ? 'Futures ₹ — ALL STOCKS is scored on futures (pricing every option would be thousands of ICICI requests); "Use" switches P&L Basis to Futures so the backtest matches'
                : data.basis_note === 'sensex'
                    ? 'Futures ₹ — SENSEX options are on BSE, not priced'
                    : 'Futures ₹ — same as the backtest');
        if (metaEl) metaEl.textContent =
            `${data.total_combos_tested} combos · ${data.total} with ≥ ${data.min_trades} trades · `
            + `${data.symbols_used} stock${data.symbols_used === 1 ? '' : 's'} · ${basisTxt}`;
        // "Use" also sets the basis the sweep scored on, so the backtest of a
        // row reproduces that row.
        _wcbOptBasis = data.basis || 'futures';
        _mountSingleOptGrid('wcbOptGrid', data.results || [], WCB_OPT_COLS, 'net_pnl_rupees', applyWcbOptResult);
        if (panel)     panel.style.display     = '';
        if (recalcBtn) recalcBtn.style.display = '';
        if (data.best) applyWcbOptResult(data.best);
    }

    let _wcbOptBasis = 'futures';
    function applyWcbOptResult(r) {
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        set('wcbBasis', _wcbOptBasis);
        set('wcbRrRatio', String(r.rr_ratio));
        set('wcbMaxRatio', String(r.max_cpr_ratio));
        set('wcbMinRange', String(r.min_range_pct));
        set('wcbDirection', r.direction);
        if (window.showNotification) window.showNotification(
            `Applied: 1:${r.rr_ratio} · W CPR ${r.max_cpr_ratio === 'any' ? 'any' : '≤ ' + r.max_cpr_ratio + '×'}`
            + ` · 1H range ${r.min_range_pct ? '≥ ' + r.min_range_pct + '%' : 'off'} · ${r.direction}`
            + ` · ${_wcbOptBasis === 'option' ? 'option' : 'futures'} basis · Win% ${r.win_rate}%`, 'success');
    }

    async function runWcbOptimise(recalculate) {
        const panel     = document.getElementById('weeklyCprBreakoutOptimisePanel');
        const recalcBtn = document.getElementById('wcbRecalcOptBtn');
        const optimBtn  = document.getElementById('runOptimiseBtn');
        const activeBtn = recalculate ? recalcBtn : optimBtn;
        const origText  = activeBtn ? activeBtn.textContent : '';
        if (activeBtn) { activeBtn.textContent = '⏳ Running…'; activeBtn.disabled = true; }
        if (panel) panel.style.display = 'none';
        const myRun = ++_wcbOptRun;
        const done = () => { if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; } };
        _showOptLoader('Finding best params…', 'Starting…');
        try {
            const resp = await fetch('/api/backtest/weekly-cpr-breakout/optimise', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol:     selectedSymbol === ALL_STOCKS ? '' : (selectedSymbol || '').toUpperCase(),
                    start_date: document.getElementById('startDate')?.value,
                    end_date:   document.getElementById('endDate')?.value,
                    // Not swept — scored under the values the single run uses.
                    exit_time:  document.getElementById('wcbExitTime')?.value || '15:20',
                    lots:       Math.max(1, parseInt(document.getElementById('wcbLots')?.value || 1)),
                    basis:      document.getElementById('wcbBasis')?.value || 'option',
            signal:     document.getElementById('wcbSignal')?.value || 'candle',
            candle_type: document.getElementById('wcbCandleType')?.value || 'all',
                }),
            });
            let data = await resp.json();
            if (!data.success || !data.task_id) throw new Error(data.error || 'Optimisation failed');
            const taskId = data.task_id;
            while (true) {
                await new Promise(r => setTimeout(r, 2500));
                if (myRun !== _wcbOptRun) return;
                data = await (await fetch(`/api/backtest/weekly-cpr-breakout/optimise/status/${taskId}`)).json();
                if (myRun !== _wcbOptRun) return;
                if (data.status !== 'running') break;
                const p = data.progress || {};
                _showOptLoader('Finding best params…',
                    `${p.done || 0} / ${p.total || 0} stocks · 4 targets each · ${data.elapsed || 0}s`);
            }
            _hideOptLoader();
            done();
            if (!data.success || data.status === 'error') throw new Error(data.error || 'Optimisation failed');
            renderWcbOptResults(data);
        } catch (err) {
            if (myRun !== _wcbOptRun) return;
            _hideOptLoader();
            done();
            console.error('Weekly CPR Breakout optimise error:', err);
            window.showNotification(err.message || 'Optimisation failed', 'error');
        }
    }
    const wcbRecalcBtn = document.getElementById('wcbRecalcOptBtn');
    if (wcbRecalcBtn) wcbRecalcBtn.addEventListener('click', () => runWcbOptimise(true));

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
        _showOptLoader('Finding best params…', 'Sweeping index / Top-N / exit-rank combos…');

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
                _hideOptLoader();
                return;
            }
            // Cached result comes back immediately — render straight away
            if (data.from_cache) {
                _hideOptLoader();
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
            _hideOptLoader();
        }
    }

    function _pollSmOptimise(taskId, activeBtn, origText, startMs) {
        const MAX_WAIT_MS = 10 * 60 * 1000; // 10 min hard stop

        function tick() {
            const elapsed = Math.round((Date.now() - startMs) / 1000);
            if (activeBtn) activeBtn.textContent = `⏳ ${elapsed}s…`;
            _showOptLoader('Finding best params…', `${elapsed}s elapsed`);

            if (Date.now() - startMs > MAX_WAIT_MS) {
                window.showNotification('Optimisation timed out — try a shorter date range', 'error');
                if (activeBtn) { activeBtn.textContent = origText; activeBtn.disabled = false; }
                _hideOptLoader();
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
                    _hideOptLoader();
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

    const _SM_IDX_SHORT = {
        'NIFTY 500': 'Nifty 500', 'NIFTY 200': 'Nifty 200',
        'NIFTY SMALLCAP 250': 'SC 250', 'NIFTY SMALLCAP 500': 'SC 500',
        'NIFTY MICROCAP 250': 'MC 250', 'NIFTY LARGEMIDCAP 250': 'LMC 250',
        'NIFTY MIDSMALLCAP 400': 'MSC 400',
    };
    const SM_OPT_COLS = [
        { label: '#',          key: null,           fmt: (r, i) => i + 1 },
        { label: 'Index',      key: 'index',         fmt: r => `<span style="white-space:nowrap">${_SM_IDX_SHORT[r.index] || r.index}</span>` },
        { label: 'Top N',      key: 'top_n',         fmt: r => r.top_n },
        { label: 'Exit Rank',  key: 'exit_rank',     fmt: r => r.exit_rank },
        { label: 'Return %',   key: 'total_return_pct', fmt: r => `<span class="${r.total_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.total_return_pct >= 0 ? '+' : '') + r.total_return_pct.toFixed(1)}%</span>` },
        { label: 'CAGR %',     key: 'cagr_pct',      fmt: r => `<span class="${r.cagr_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">${(r.cagr_pct >= 0 ? '+' : '') + r.cagr_pct.toFixed(1)}%</span>` },
        { label: 'Max DD %',   key: 'max_drawdown_pct', fmt: r => `<span class="pnl-negative">${r.max_drawdown_pct.toFixed(1)}%</span>` },
        { label: 'Score',      key: 'score',         fmt: r => r.score.toFixed(2) },
        { label: '',           key: null,            fmt: () => '' },
    ];

    function _renderSmOptResults(data) {
        const panel     = document.getElementById('smOptimisePanel');
        const metaEl    = document.getElementById('smOptMeta');
        const recalcBtn = document.getElementById('smRecalcOptBtn');

        if (metaEl) {
            const freqLabel = (data.rebalance_freq || 'monthly');
            const freqDisp  = freqLabel.charAt(0).toUpperCase() + freqLabel.slice(1);
            let meta = `3 indices · ${freqDisp} · ${data.total_combos_tested} combos · ${data.start_date} → ${data.end_date}`;
            if (data.from_cache && data.cached_at) meta += ` · cached ${data.cached_at}`;
            metaEl.textContent = meta;
        }

        // 'score' is the server's own composite ranking metric — sorting by it
        // descending reproduces the original "row 0 is the best combo" default,
        // since the server already returns results in that order.
        _mountSingleOptGrid('smOptGrid', data.results || [], SM_OPT_COLS, 'score', _applySmOptResult);

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
            <label class="sm-gl-field sm-gl-field-wide"><span>Default broker (optional)</span>
                <select id="glBroker"><option value="">None — choose later</option></select></label>
        </div>
        <div class="sm-gl-note" id="glBrokerNote">
            Go Live only saves this portfolio to <strong>Algo → Swing Momentum</strong> (tracked at
            current ranking prices). Place the real <strong>CNC MARKET</strong> orders later with the
            <strong>Place Orders</strong> button on the Live Algo screen. The broker picked here is just
            the default for that screen.
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
        cbtn.textContent = 'Saving…';

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

        // Row 2 (per-trade cards): hide
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
        _lastSmEquityArgs = { curve, investment };
        _lastEquityArgs = null;
        const chartColors = _btChartColors();

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
                        ticks: { maxTicksLimit: 15, color: chartColors.tick, font: { size: 11 }, autoSkip: true },
                        grid:  { color: chartColors.grid },
                    },
                    y: {
                        ticks: { color: chartColors.tick, font: { size: 11 }, callback: fmtY },
                        grid:  { color: chartColors.grid },
                    }
                }
            }
        });

        section.style.display = '';
    }

    const _fmtSmRs = v => '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const SM_TRADES_COLS = [
        { key: 'date', label: 'Date', sortable: true, format: v => v || '—' },
        { key: 'symbol', label: 'Symbol', sortable: true, strong: true, format: v => v || '—' },
        { key: 'action', label: 'Action', sortable: true,
          render: (v) => DataGrid.badge(v, v === 'BUY' ? 'pos' : 'neg') },
        { key: 'qty', label: 'Qty', sortable: true, format: v => v ?? 0 },
        { key: 'price', label: 'Price', sortable: true, format: v => _fmtSmRs(v || 0) },
        { key: 'investment', label: '₹ Value', sortable: true, format: v => _fmtSmRs(v || 0) },
        { key: 'reason', label: 'Reason', sortable: true, format: v => v || '—' },
        { key: 'rank', label: 'Rank', sortable: true, format: v => v != null ? v : '—' },
        { key: 'pnl', label: 'P&L', sortable: true, strong: true, tone: DataGrid.sign,
          format: v => v != null ? (v >= 0 ? '+' : '') + _fmtSmRs(v) : '—' },
    ];

    function _renderSmTable(trades) {
        DataGrid.mountSortable('tradesGrid', {
            rows: trades || [],
            columns: SM_TRADES_COLS,
            empty: 'No trades generated',
            // BUY/SELL tint: a translucent wash of the same tone as the Action
            // badge, so a long list reads by side at a glance.
            rowClass: t => t.action === 'BUY' ? 'sm-trade-buy' : 'sm-trade-sell',
        });
    }
});
