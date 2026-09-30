/**
 * cpr_filter.js
 * Handles fetching, displaying, and sorting CPR filter data.
 */

// Cached DOM references (populated on DOMContentLoaded)
const cprElems = {};

// Auto-load data when page loads
window.addEventListener('load', function () {
    // Cache frequently-used DOM refs once (avoids repeated getElementById calls)
    cprElems.statusBar   = document.getElementById('status-text');
    cprElems.datePicker  = document.getElementById('cprDateFilter');
    cprElems.highIvRefreshBtn = document.getElementById('highIvRefreshBtn');
    cprElems.highIvResults = document.getElementById('highIvResults');
    cprElems.highIvCount = document.getElementById('highIvCount');
    cprElems.controls    = document.getElementById('controls');
    cprElems.tpoSinglesRefreshBtn = document.getElementById('tpoSinglesRefreshBtn');
    cprElems.narrowCprTimeframe  = document.getElementById('narrowCprTimeframe');
    cprElems.narrowCprRatio      = document.getElementById('narrowCprRatio');
    cprElems.narrowCprDate       = document.getElementById('narrowCprDateFilter');
    cprElems.narrowCprRefreshBtn = document.getElementById('narrowCprRefreshBtn');

    const scheduler = window.CPRFilterScheduler;
    const schedulerActive = scheduler && typeof scheduler.isActive === 'function' && scheduler.isActive();
    const schedulerMarketOpen = scheduler && typeof scheduler.isMarketOpen === 'function' && scheduler.isMarketOpen();

    // Initialize date picker with today's date. Changing it does not re-run
    // the scan on its own — loadHighIVData reads .value when the High IV
    // button is pressed.
    if (cprElems.datePicker) {
        cprElems.datePicker.value = new Date().toISOString().split('T')[0];
    }

    // Initialize High IV Refresh Button
    if (cprElems.highIvRefreshBtn) {
        cprElems.highIvRefreshBtn.addEventListener('click', () => {
            const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
            loadHighIVData(selectedDate, true);
        });
    }

    // TPO single prints: the scan judges the session ON the selected date,
    // so moving the date has to re-run it rather than re-filter what is
    // already on screen. Not forced — the scan is cached per session date,
    // so a date it has not read scans anyway and one it has is instant.
    if (cprElems.tpoSinglesRefreshBtn) {
        cprElems.tpoSinglesRefreshBtn.addEventListener('click', () => {
            loadTpoSinglesData(true);
        });
    }
    if (cprElems.datePicker) {
        cprElems.datePicker.addEventListener('change', () => {
            if (_tpoSinglesLoaded) loadTpoSinglesData(false);
        });
    }

    // Clicking a scanner symbol opens the shared candle popup rather than
    // leaving for TradingView. Delegated, because DataGrid rebuilds every
    // row on each sort and a per-row listener would not survive it.
    ['narrowCpr', 'tpoSingles'].forEach((gridType) => {
        const grid = document.getElementById(`${gridType}Grid`);
        if (!grid) return;
        grid.addEventListener('click', (e) => {
            const link = e.target.closest('[data-candle]');
            // Let ctrl/cmd/middle-click through to TradingView.
            if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            _openCandleModal(link.dataset.candle, gridType);
        });
    });

    // Narrow CPR lives on its own tab now, with its own date picker: the scan
    // judges the CPR as of that date, so changing it re-runs the load rather
    // than re-filtering what is on screen. Not a forced refresh — the scan is
    // cached per date, so a date it has not seen scans anyway and one it has
    // is instant.
    if (cprElems.narrowCprDate) {
        const today = new Date().toISOString().split('T')[0];
        cprElems.narrowCprDate.value = today;
        cprElems.narrowCprDate.addEventListener('change', () => {
            if (_narrowCprLoaded) loadNarrowCprData(_narrowCprTf(), false);
        });
    }

    // The scan itself does not depend on the timeframe picker — it reads
    // weekly AND monthly for every symbol — so changing the picker only
    // re-filters what the server already has cached.
    if (cprElems.narrowCprRefreshBtn) {
        cprElems.narrowCprRefreshBtn.addEventListener('click', () => {
            _narrowCprLoaded = true;
            loadNarrowCprData(_narrowCprTf(), true);
        });
    }
    // Both pickers re-filter the same cached scan — neither re-reads history.
    [cprElems.narrowCprTimeframe, cprElems.narrowCprRatio].forEach((el) => {
        if (el) el.addEventListener('change', () => {
            if (_narrowCprLoaded) loadNarrowCprData(_narrowCprTf(), false);
        });
    });

    // Avoid double-triggering the API when the scheduler is already running during market hours
    if (schedulerActive && schedulerMarketOpen) {
        if (cprElems.statusBar) cprElems.statusBar.textContent = '⏳ Scheduler active - waiting for next run...';
    } else {
        if (cprElems.statusBar) cprElems.statusBar.textContent = '⏳ Loading initial data...';
        const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
        loadHighIVData(selectedDate, false);
    }

    // Click-to-sort is owned by DataGrid.mountSortable now — see displayResults().
});

// A grid container (a plain <div>, not a <table>) shows its own loading /
// error state directly — no fake colspan <tr> needed once the table itself
// is built by DataGrid.
function _gridLoadingHtml(label) {
    return `<div class="cpr-grid-status">
        <span class="cpr-grid-spinner"></span>${DataGrid.escape(label)}
    </div>`;
}
function _gridErrorHtml(label) {
    return `<div class="cpr-grid-status cpr-grid-status--error">❌ ${DataGrid.escape(label)}</div>`;
}

/**
 * Fetches High IV Percentile data from the backend API separately.
 */
async function loadHighIVData(selectedDate, refresh = false) {
    const highIvGrid = document.getElementById('highIvGrid');
    const highIvResultsDiv = cprElems.highIvResults || document.getElementById('highIvResults');
    const highIvCountSpan = cprElems.highIvCount || document.getElementById('highIvCount');

    if (!highIvGrid || !highIvResultsDiv || !highIvCountSpan) {
        return;
    }

    const highIvRefreshBtn = cprElems.highIvRefreshBtn;
    if (highIvRefreshBtn) {
        highIvRefreshBtn.classList.add('loading');
        highIvRefreshBtn.disabled = true;
    }

    // Show High IV block and set loading indicator
    highIvResultsDiv.classList.remove('results-hidden');
    highIvCountSpan.textContent = '...';
    highIvGrid.innerHTML = _gridLoadingHtml(
        '⚡ Scanning option chains and computing 1-year Historical Volatility percentile rankings...');

    try {
        let url = '/api/cpr-filter/high-iv';
        const params = [];
        if (selectedDate) {
            params.push(`date=${selectedDate}`);
        }
        if (refresh) {
            params.push('refresh=true');
        }
        if (params.length > 0) {
            url += `?${params.join('&')}`;
        }

        const response = await fetchJson(url);
        if (response && response.success) {
            const highIvStocks = response.high_iv_stocks || [];
            displayResults('highIv', highIvStocks);
        } else {
            highIvGrid.innerHTML = _gridErrorHtml('Failed to load High IV percentile data.');
            highIvCountSpan.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching High IV data:', error);
        highIvGrid.innerHTML = _gridErrorHtml('Error: ' + error.message);
        highIvCountSpan.textContent = '(0)';
    } finally {
        if (highIvRefreshBtn) {
            highIvRefreshBtn.classList.remove('loading');
            highIvRefreshBtn.disabled = false;
        }
    }
}

// The rows behind each popup-opening grid, kept so the popup's ‹ › can step
// through what is on screen. Only the fields CandleModal reads.
const _gridRows = { narrowCpr: [], tpoSingles: [] };

function _openCandleModal(symbol, gridType) {
    if (!window.CandleModal) return;
    const rows = _gridRows[gridType] || [];
    if (gridType !== 'tpoSingles') {
        window.CandleModal.open(symbol, { rows });
        return;
    }
    // A single-print row is only meaningful next to the profile that drew
    // it, so this one opens straight into TPO mode: 30-minute periods, and
    // the row grid the scan measured the band on (tpoPin.rowStep) rather
    // than the one the chart would pick for whatever window is on screen.
    // Different grids put the same gap at different prices.
    const row = rows.find((r) => r.symbol === symbol) || {};
    window.CandleModal.open(symbol, {
        rows, interval: '30m', tpo: true, tpoPin: row.tpoPin || {},
    });
}

const _NARROW_TF_LABEL = {
    both:    'Weekly + Monthly',
    weekly:  'Weekly',
    monthly: 'Monthly',
};

function _narrowCprTf() {
    const v = cprElems.narrowCprTimeframe ? cprElems.narrowCprTimeframe.value : 'both';
    return v in _NARROW_TF_LABEL ? v : 'both';
}

// How far below its own normal the CPR has to sit. The server owns the list of
// accepted values and falls back to its default for anything else.
function _narrowCprRatio() {
    return cprElems.narrowCprRatio ? cprElems.narrowCprRatio.value : '0.3';
}

// The Narrow CPR tab loads on first open, not on page load — the scan is the
// expensive one here and the page no longer opens on it.
let _narrowCprLoaded = false;
function initNarrowCprOnce() {
    if (_narrowCprLoaded) return;
    _narrowCprLoaded = true;
    // The tab can be opened straight from ?tab=narrow-cpr on DOMContentLoaded,
    // which is before the load handler above has cached this tab's controls —
    // wait for it, so the scan reads the pickers and not their fallbacks.
    if (cprElems.narrowCprRefreshBtn) {
        loadNarrowCprData(_narrowCprTf(), false);
    } else {
        window.addEventListener('load',
            () => loadNarrowCprData(_narrowCprTf(), false), { once: true });
    }
}

/**
 * Fetches the Narrow CPR scan. The CPR read is the CURRENT one — built from
 * the week / month being traded now, still forming mid-period — and "narrow"
 * means narrow against that symbol's own recent CPR widths, not a fixed
 * percentage. `tf` only says which of the two readings has to be Narrow for a
 * row to show.
 */
async function loadNarrowCprData(tf, refresh = false) {
    tf = tf in _NARROW_TF_LABEL ? tf : 'both';

    const grid     = document.getElementById('narrowCprGrid');
    const container = document.getElementById('narrowCprResults');
    const countSpan = document.getElementById('narrowCprCount');
    const titleSpan = document.getElementById('narrowCprTitle');
    const footnote  = document.getElementById('narrowCprFootnote');
    if (!grid || !container) return;

    const ratio = _narrowCprRatio();
    if (titleSpan) titleSpan.textContent =
        `📏 Narrow CPR — ${_NARROW_TF_LABEL[tf]} ≤ ${ratio}× normal`;

    const refreshBtn = cprElems.narrowCprRefreshBtn;
    if (refreshBtn) {
        refreshBtn.classList.add('loading');
        refreshBtn.disabled = true;
    }

    container.classList.remove('results-hidden');
    if (countSpan) countSpan.textContent = '(...)';
    if (footnote) footnote.textContent = '';
    grid.innerHTML = _gridLoadingHtml(
        `Reading the current weekly and monthly CPR widths `
        + `(${_NARROW_TF_LABEL[tf]} at or under ${ratio}x its own normal)...`);

    try {
        const selectedDate = cprElems.narrowCprDate ? cprElems.narrowCprDate.value : null;
        let url = `/api/cpr-filter/narrow-cpr?tf=${tf}&ratio=${encodeURIComponent(ratio)}`;
        if (selectedDate) url += `&date=${selectedDate}`;
        if (refresh) url += '&refresh=true';

        const response = await fetchJson(url);
        if (response && response.success) {
            const rows = response.rows || [];
            displayResults('narrowCpr', rows);
            if (footnote) {
                footnote.textContent = `${rows.length} of ${response.scanned || 0} symbols read`
                    + ` · CPR at or under ${response.ratio}x its own normal`
                    + (response.skipped ? ` · ${response.skipped} skipped (no usable history)` : '');
            }
        } else {
            grid.innerHTML = _gridErrorHtml('Failed to load Narrow CPR data.');
            if (countSpan) countSpan.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching Narrow CPR data:', error);
        grid.innerHTML = _gridErrorHtml('Error: ' + error.message);
        if (countSpan) countSpan.textContent = '(0)';
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove('loading');
            refreshBtn.disabled = false;
        }
    }
}

// The TPO scan is the only one on the High IV tab that is not run on load:
// it reads one session of 30-minute bars for every futures stock, which is
// a request per symbol, and most visits to this tab are for the IV list.
let _tpoSinglesLoaded = false;

/**
 * Fetches the TPO single-print scan for the date picker's session.
 *
 * A single print is a run of rows only ONE 30-minute period reached with
 * busier rows on both sides — never the profile's own top or bottom, which
 * is the session's tail. The server ranks the matches; this only draws them.
 */
async function loadTpoSinglesData(refresh = false) {
    const grid      = document.getElementById('tpoSinglesGrid');
    const container = document.getElementById('tpoSinglesResults');
    const countSpan = document.getElementById('tpoSinglesCount');
    const titleSpan = document.getElementById('tpoSinglesTitle');
    const footnote  = document.getElementById('tpoSinglesFootnote');
    if (!grid || !container) return;

    _tpoSinglesLoaded = true;
    const refreshBtn = cprElems.tpoSinglesRefreshBtn;
    if (refreshBtn) {
        refreshBtn.classList.add('loading');
        refreshBtn.disabled = true;
    }

    container.classList.remove('results-hidden');
    if (countSpan) countSpan.textContent = '(...)';
    if (footnote) footnote.textContent = '';
    grid.innerHTML = _gridLoadingHtml(
        'Building a 30-minute market profile for every futures stock and reading '
        + 'its single prints...');

    try {
        const selectedDate = cprElems.datePicker ? cprElems.datePicker.value : null;
        let url = '/api/cpr-filter/tpo-singles';
        const params = [];
        if (selectedDate) params.push(`date=${selectedDate}`);
        if (refresh) params.push('refresh=true');
        if (params.length) url += `?${params.join('&')}`;

        const response = await fetchJson(url);
        if (response && response.success) {
            const rows = response.rows || [];
            if (titleSpan) titleSpan.textContent =
                `📐 TPO Single Prints — 30-min periods, ${response.date} session`;
            displayResults('tpoSingles', rows);
            if (footnote) {
                footnote.textContent = `${rows.length} of ${response.scanned || 0} symbols`
                    + ` printed a single on ${response.date}`
                    + (response.skipped ? ` · ${response.skipped} skipped (no intraday history)` : '');
            }
        } else {
            grid.innerHTML = _gridErrorHtml('Failed to load TPO single-print data.');
            if (countSpan) countSpan.textContent = '(0)';
        }
    } catch (error) {
        console.error('Error fetching TPO single-print data:', error);
        grid.innerHTML = _gridErrorHtml('Error: ' + error.message);
        if (countSpan) countSpan.textContent = '(0)';
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove('loading');
            refreshBtn.disabled = false;
        }
    }
}

// Symbol column — a TradingView link, shared by every scanner grid on this page.
function _fmtVol(v) {
    v = Number(v || 0);
    if (v >= 10000000) return (v / 10000000).toFixed(1) + 'Cr';
    if (v >= 100000)   return (v / 100000).toFixed(1) + 'L';
    if (v >= 1000)     return (v / 1000).toFixed(1) + 'K';
    return v.toString();
}
function _symbolColumn() {
    return {
        key: 'symbol', label: 'Symbol', sortable: true, strong: true,
        render: (symbol) => `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
            `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
            `class="symbol-link">${DataGrid.escape(symbol)}</a>`,
    };
}
// Symbol column for the grids whose rows open the in-app candle popup.
// data-candle is what the delegated grid click handler looks for. Still an
// <a> to TradingView underneath, so ctrl/middle-click opens the real chart
// there — a plain click is intercepted and opens the popup instead.
function _candleSymbolColumn() {
    return {
        key: 'symbol', label: 'Symbol', sortable: true, strong: true,
        render: (symbol, row) => {
            const link = `<a href="https://in.tradingview.com/chart/?symbol=NSE:` +
                `${encodeURIComponent(symbol)}" target="_blank" rel="noopener noreferrer" ` +
                `class="symbol-link" data-candle="${DataGrid.escape(symbol)}"` +
                `>${DataGrid.escape(symbol)}</a>`;
            return row && row.is_index
                ? `${link} <span class="dg-badge dg-badge--neutral">IDX</span>` : link;
        },
    };
}

// gap-up/gap-down are this page's own theme-aware up/down colours (kept as
// page CSS, not the grid's dg-pos/dg-neg, so nothing here drifts from the
// rest of the scanner's palette).
const _upDownClass = (v) => Number(v || 0) > 0 ? 'gap-up' : Number(v || 0) < 0 ? 'gap-down' : '';

// ── Per-table column configs ────────────────────────────────────────
const _SCANNER_COLUMNS = {
    highIv: () => [
        _symbolColumn(),
        { key: 'current_price', label: 'Price', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'iv_percentile', label: 'IV Pctl %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%',
          cellClass: v => Number(v || 0) >= 90 ? 'iv-very-high' : 'iv-high' },
        { key: 'atm_iv', label: 'ATM IV %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%' },
        { key: 'day_change_pct', label: 'Day Chg %', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) + '%', cellClass: _upDownClass },
        { key: 'volume', label: 'Volume', sortable: true, align: 'right', format: _fmtVol },
        { key: 'oi_change_pct', label: 'OI% Chg', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(1) + '%', cellClass: _upDownClass },
        { key: 'pcr', label: 'PCR', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2),
          cellClass: v => Number(v || 0) > 1 ? 'gap-up' : Number(v || 0) < 0.7 ? 'gap-down' : '' },
        { key: 'max_pain', label: 'Max Pain', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(0) },
    ],
    // TPO single prints. The band column is the whole point of the row, so
    // it is wide and early; the levels after it are the profile the band sits
    // in, which is what says whether the gap is above or below value.
    tpoSingles: () => [
        _candleSymbolColumn(),
        { key: 'current_price', label: 'Close', sortable: true, align: 'right', strong: true,
          format: v => Number(v || 0).toFixed(2) },
        { key: 'single_count', label: 'Singles', sortable: true, align: 'right',
          badge: () => 'warn' },
        { key: 'bands', label: 'Band(s)', sortable: true,
          title: (_v, row) => `Row grid ${row.row_step} · ${row.periods} periods`
              + ` · session ${row.session_low}–${row.session_high}` },
        { key: 'tallest_pct', label: 'Tallest %', sortable: true, align: 'right', strong: true,
          format: v => Number(v || 0).toFixed(2) + '%',
          title: (_v, row) => `${row.tallest_pts} points` },
        { key: 'vah', label: 'VAH', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'poc', label: 'POC', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'val', label: 'VAL', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'session_high', label: 'High', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'session_low', label: 'Low', sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: 'periods', label: 'Periods', sortable: true, align: 'right' },
    ],
    // Narrow CPR — both readings are on every row whatever the dropdown says,
    // so a row narrow on one timeframe still shows what the other is doing.
    narrowCpr: () => [
        _candleSymbolColumn(),
        { key: 'current_price', label: 'Close', sortable: true, align: 'right', strong: true,
          format: v => Number(v || 0).toFixed(2) },
        { key: 'narrow_on', label: 'Narrow', sortable: true,
          badge: v => v === 'Weekly + Monthly' ? 'pos' : 'warn' },
        ..._narrowTfColumns('weekly', 'W'),
        ..._narrowTfColumns('monthly', 'M'),
    ],
};

// The five columns each timeframe contributes to the Narrow CPR grid.
// Width is |TC - BC| / close * 100 for the CURRENT week / month; ×avg is that
// width over the symbol's own average across the periods before it, which is
// what actually decides Narrow — under 0.80x. "—" means too little history to
// average, and the label then came from the absolute fallback scale.
//
// A period still forming carries a "live" badge: its OHLC, and so its width,
// moves until it closes. Its context is measured over the same number of
// sessions (see _width_of_first in the scanner), so an early-month reading is
// compared against equally young months rather than finished ones.
function _narrowTfColumns(tf, prefix) {
    const periodTitle = (_v, row) => `CPR from ${row[`${tf}_period`]}`
        + ` · ${row[`${tf}_bars`]} sessions${row[`${tf}_forming`] ? ', still forming' : ', closed'}`
        + ` · ${row[`${tf}_context`]} periods of context`;
    return [
        { key: `${tf}_width_pct`, label: `${prefix} Width %`, sortable: true, align: 'right',
          render: (v, row) => `${Number(v || 0).toFixed(3)}%` + (row[`${tf}_forming`]
              ? ' <span class="dg-badge dg-badge--neutral">live</span>' : ''),
          title: periodTitle },
        { key: `${tf}_ratio`, label: `${prefix} ×avg`, sortable: true, align: 'right', strong: true,
          format: v => v == null ? '—' : Number(v).toFixed(2) + '×',
          tone: (_v, row) => row[`narrow_${tf}`] ? 'pos'
              : row[`${tf}_type`] === 'Wide' ? 'neg' : 'muted',
          title: (_v, row) => `${row[`${tf}_type`]} — ${Number(row[`${tf}_width_pct`]).toFixed(3)}%`
              + ` vs an average ${row[`${tf}_avg_width_pct`] == null ? 'n/a'
                  : Number(row[`${tf}_avg_width_pct`]).toFixed(3) + '%'}` },
        { key: `${tf}_bc`, label: `${prefix} BC`, sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: `${tf}_pp`, label: `${prefix} Pivot`, sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
        { key: `${tf}_tc`, label: `${prefix} TC`, sortable: true, align: 'right',
          format: v => Number(v || 0).toFixed(2) },
    ];
}

/**
 * Populates a scanner grid with data.
 * @param {string} type - The grid type identifier ('highIv', 'narrowCpr',
 *                        'tpoSingles').
 * @param {Array<Object>} results - The list of stock objects.
 */
function displayResults(type, results) {
    const grid = document.getElementById(`${type}Grid`);
    const container = document.getElementById(`${type}Results`);
    const countSpan = document.getElementById(`${type}Count`);

    if (!grid || !container || !countSpan) return;

    if (!Array.isArray(results)) {
        console.error(`Invalid results for type '${type}':`, results);
        results = [];
    }

    if (type in _gridRows) {
        // tpoPin travels with the row: the popup has to draw the profile on
        // the grid this scan measured the band on, or the band it shows is
        // not the band in the grid. Carried per row because each session
        // sizes its own rows from its own range.
        _gridRows[type] = results.map((r) => ({
            symbol: r.symbol,
            tpoPin: { rowStep: r.row_step },
        }));
    }

    if (results.length === 0) {
        grid.innerHTML = '';
        container.classList.add('results-hidden');
        countSpan.textContent = '(0)';
        return;
    }

    const columns = type === 'highIv'     ? _SCANNER_COLUMNS.highIv()
        : type === 'narrowCpr'  ? _SCANNER_COLUMNS.narrowCpr()
        : type === 'tpoSingles' ? _SCANNER_COLUMNS.tpoSingles()
        : null;
    if (!columns) return;

    DataGrid.mountSortable(grid, { rows: results, columns, empty: 'No matches.' });

    container.classList.remove('results-hidden');
    countSpan.textContent = `(${results.length})`;
}

// Click-to-sort is DataGrid.mountSortable's job now (see displayResults()) —
// the bespoke column-index/text-scraping sorter that used to live here is gone.

