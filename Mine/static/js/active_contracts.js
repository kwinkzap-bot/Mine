(function () {
    'use strict';

    let _underlying  = 'NIFTY';
    let _expiry      = '';
    let _optionType  = 'all';
    let _strike      = '';
    let _data        = null;
    let _wired       = false;
    let _pollTimer   = null;

    const POLL_MS = 60 * 1000;   // auto-refresh every one minute

    const $ = id => document.getElementById(id);

    // ── Public init called by dashSwitch ──────────────────────────
    window.acInit = function () {
        wire();
        if (!window._acLoaded) fetchContracts();
        startPolling();
    };

    // ── Auto-refresh every minute (silent, pauses when tab hidden) ─
    function startPolling() {
        if (_pollTimer) return;
        _pollTimer = setInterval(() => {
            if (document.hidden) return;   // skip while tab not visible
            fetchContracts(true);          // silent refresh — keep filters/strike
        }, POLL_MS);
    }

    function wire() {
        if (_wired) return;
        _wired = true;

        $('acUnderlying').addEventListener('change', function () {
            _underlying = this.value;
            _expiry = '';
            _strike = '';
            window._acLoaded = false;
            fetchContracts();
        });

        $('acExpiry').addEventListener('change', function () {
            _expiry = this.value;
            _strike = '';
            fetchContracts();
        });

        $('acOptionType').addEventListener('change', function () {
            _optionType = this.value;
            _strike = '';
            fetchContracts();
        });

        $('acStrike').addEventListener('change', function () {
            _strike = this.value;
            render();
        });

        $('acRefreshBtn').addEventListener('click', () => {
            window._acLoaded = false;
            fetchContracts();
        });

        $('acClearBtn').addEventListener('click', () => {
            _optionType = 'all';
            _strike = '';
            $('acOptionType').value = 'all';
            $('acStrike').value = '';
            render();
        });
    }

    // ── Fetch ──────────────────────────────────────────────────────
    function fetchContracts(silent) {
        if (!silent) {
            $('acLoading').classList.remove('hidden');
            $('acTableWrap').classList.add('hidden');
            $('acError').classList.add('hidden');
            $('acStatTotal').textContent = '';
        }

        const params = new URLSearchParams({
            underlying: _underlying,
            type:       _optionType,
        });
        if (_expiry) params.set('expiry', _expiry);

        fetch('/api/active-contracts?' + params)
            .then(r => r.json())
            .then(data => {
                $('acLoading').classList.add('hidden');
                if (!data.success) throw new Error(data.error || 'Unknown error');
                _data = data;
                window._acLoaded = true;

                // Set active expiry from server (nearest by default)
                if (!_expiry && data.active_expiry) _expiry = data.active_expiry;

                populateExpiryDropdown(data.expiry_options, data.active_expiry);
                populateStrikeDropdown(data.contracts);
                renderAnalysis(data.analysis, data.spot, data.underlying);
                $('acTableWrap').classList.remove('hidden');
                render();
            })
            .catch(err => {
                if (silent) return;   // keep last good data on a transient poll failure
                $('acLoading').classList.add('hidden');
                $('acTableWrap').classList.remove('hidden');
                const el = $('acError');
                el.textContent = 'Error: ' + err.message;
                el.classList.remove('hidden');
            });
    }

    // ── Dropdowns ──────────────────────────────────────────────────
    function populateExpiryDropdown(options, activeExpiry) {
        const sel = $('acExpiry');
        sel.innerHTML = '';
        (options || []).forEach(e => {
            const opt = document.createElement('option');
            opt.value = e.date;
            opt.textContent = e.label;
            sel.appendChild(opt);
        });
        sel.value = activeExpiry || (options && options[0] ? options[0].date : '');
        _expiry = sel.value;
    }

    function populateStrikeDropdown(contracts) {
        const sel = $('acStrike');
        const prev = _strike;
        const strikes = Array.from(new Set(
            (contracts || [])
                .filter(c => c.strike != null)
                .map(c => Number(c.strike))
        )).sort((a, b) => a - b);

        sel.innerHTML = '<option value="">Strike Price</option>';
        strikes.forEach(s => {
            const opt = document.createElement('option');
            opt.value = String(Math.round(s));
            opt.textContent = fmtNum(s, 0);
            sel.appendChild(opt);
        });
        _strike = strikes.some(s => String(Math.round(s)) === prev) ? prev : '';
        sel.value = _strike;
    }

    // ── Market direction panel ─────────────────────────────────────
    function renderAnalysis(a, spot, underlying) {
        const box = $('acAnalysis');
        if (!box) return;

        if (!a) {
            box.classList.add('hidden');
            box.innerHTML = '';
            return;
        }

        box.classList.remove('hidden');

        if (!a.available) {
            box.innerHTML = '<div class="ac-ana-unavailable">' + esc(a.reason || 'Direction analysis unavailable') + '</div>';
            return;
        }

        const dirClass = a.direction === 'UP' ? 'up' : a.direction === 'DOWN' ? 'down' : 'side';
        const dirIcon  = a.direction === 'UP' ? '▲' : a.direction === 'DOWN' ? '▼' : '◆';
        const dirLabel = a.direction === 'SIDEWAYS' ? 'SIDEWAYS' : 'MARKET ' + a.direction;

        let spotChip = '';
        if (spot && spot.last) {
            const sCls  = spot.pct_change > 0 ? 'ac-pos' : spot.pct_change < 0 ? 'ac-neg' : '';
            const sSign = spot.pct_change > 0 ? '+' : '';
            spotChip = `<span class="ac-ana-chip ac-ana-spot">${esc(underlying || '')} <b>${fmtNum(spot.last, 2)}</b>
                <span class="${sCls}">${sSign}${fmtNum(Math.abs(spot.pct_change), 2)}%</span></span>`;
        }

        const chips = [
            `<span class="ac-ana-chip">Vol PCR <b>${a.volume_pcr}</b></span>`,
            `<span class="ac-ana-chip">CE Move <b class="${a.ce_move_pct > 0 ? 'ac-pos' : a.ce_move_pct < 0 ? 'ac-neg' : ''}">${a.ce_move_pct > 0 ? '+' : ''}${a.ce_move_pct}%</b></span>`,
            `<span class="ac-ana-chip">PE Move <b class="${a.pe_move_pct > 0 ? 'ac-pos' : a.pe_move_pct < 0 ? 'ac-neg' : ''}">${a.pe_move_pct > 0 ? '+' : ''}${a.pe_move_pct}%</b></span>`,
            a.support    ? `<span class="ac-ana-chip">Support <b>${fmtNum(a.support, 0)}</b></span>`    : '',
            a.resistance ? `<span class="ac-ana-chip">Resistance <b>${fmtNum(a.resistance, 0)}</b></span>` : '',
        ].join('');

        const sigRows = (a.signals || []).map(s =>
            `<li class="ac-sig-row"><span class="ac-sig-dot ${s.verdict}"></span>
             <span class="ac-sig-name">${esc(s.name)}</span>
             <span class="ac-sig-detail">${esc(s.detail)}</span></li>`
        ).join('');

        box.innerHTML = `
            <div class="ac-ana-head">
                <span class="ac-dir-badge ${dirClass}">${dirIcon} ${dirLabel}</span>
                <span class="ac-ana-conf">${a.confidence_pct}% strength</span>
                ${spotChip}
                <span class="ac-ana-chips">${chips}</span>
            </div>
            <ul class="ac-sig-list">${sigRows}</ul>`;
    }

    // ── Render ─────────────────────────────────────────────────────
    function render() {
        if (!_data) return;

        let contracts = _data.contracts || [];

        // Client-side strike filter
        if (_strike) {
            contracts = contracts.filter(c =>
                c.strike != null && String(Math.round(c.strike)) === _strike
            );
        }

        const total = contracts.length;
        $('acStatTotal').textContent = total ? total.toLocaleString('en-IN') + ' contracts' : '';

        const grid = $('acGrid');
        if (!total) {
            if (grid) grid.innerHTML = '';
            $('acEmpty').classList.remove('hidden');
            return;
        }
        $('acEmpty').classList.add('hidden');

        // Pre-compute top 6 slice once
        const top6 = contracts.slice(0, 6);

        // Straddle strikes: only strikes where BOTH CE and PE appear in the top 6
        const top6CeStrikes = new Set(top6.filter(c => c.option_type === 'CE' && c.strike != null).map(c => c.strike));
        const top6PeStrikes = new Set(top6.filter(c => c.option_type === 'PE' && c.strike != null).map(c => c.strike));
        const straddleStrikes = new Set([...top6CeStrikes].filter(s => top6PeStrikes.has(s)));

        // Single CE with highest strike and PE with lowest strike in top 6
        const ceRows = top6.filter(c => c.option_type === 'CE' && c.strike != null);
        const peRows = top6.filter(c => c.option_type === 'PE' && c.strike != null);
        const topCeStrike = ceRows.length ? Math.max(...ceRows.map(c => c.strike)) : null;
        const topPeStrike = peRows.length ? Math.min(...peRows.map(c => c.strike)) : null;

        // Top-6 CE/PE flags — used by both the Strike and High columns.
        const topFlags = (c, idx) => {
            const isTop = idx < 6;
            return {
                isTopCe: isTop && c.option_type === 'CE' && c.strike === topCeStrike,
                isTopPe: isTop && c.option_type === 'PE' && c.strike === topPeStrike,
                isStraddle: isTop && c.strike != null && straddleStrikes.has(c.strike),
            };
        };

        // Shared grid (DataGrid). NOT sortable: the server returns rows
        // volume-ranked and the whole top-6/straddle read is positional —
        // reordering would highlight the wrong rows.
        grid.innerHTML = DataGrid.render({
            rows: contracts,
            rowClass: (c, idx) => idx < 6 ? 'ac-top-row' : '',
            columns: [
                { label: '', thClass: 'ac-th-info', cellClass: 'ac-info-cell',
                  render: (_, c) => `<span class="ac-info-icon" title="${esc(c.symbol)}">ℹ</span>` },
                { key: 'instrument_type', label: 'Instrument Type', cellClass: 'ac-inst-type' },
                { key: 'expiry', label: 'Expiry Date', cellClass: 'ac-expiry-col',
                  format: v => fmtDate(v) },
                { key: 'option_type', label: 'Option',
                  render: v => v
                      ? `<span class="ac-type-badge ${v.toLowerCase()}">${esc(v)}</span>`
                      : '<span class="ac-dash">—</span>' },
                { label: 'Strike', align: 'right', cellClass: 'ac-strike',
                  render: (_, c, idx) => {
                      const f = topFlags(c, idx);
                      const strikeTxt = c.strike != null ? fmtNum(c.strike, 2) : '—';
                      const strikeClass = f.isTopCe ? 'ac-strike-ce' : f.isTopPe ? 'ac-strike-pe' : '';
                      return f.isStraddle
                          ? `<span class="ac-straddle-box ${strikeClass}">${strikeTxt}</span>`
                          : `<span class="${strikeClass}">${strikeTxt}</span>`;
                  } },
                { key: 'open', label: 'Open', align: 'right', cellClass: 'ac-num',
                  format: v => fmtNum(v, 2) },
                { key: 'high', label: 'High', align: 'right',
                  cellClass: (_, c, idx) => {
                      const f = topFlags(c, idx);
                      return 'ac-num ' + ((f.isTopCe || f.isTopPe) ? 'ac-high ac-high-bold' : 'ac-high');
                  },
                  format: v => fmtNum(v, 2) },
                { key: 'low', label: 'Low', align: 'right', cellClass: 'ac-num ac-low',
                  format: v => fmtNum(v, 2) },
                { key: 'close', label: 'Close', align: 'right', cellClass: 'ac-num',
                  format: v => fmtNum(v, 2) },
                { key: 'prev_close', label: 'Prev. Close', align: 'right', cellClass: 'ac-num',
                  format: v => fmtNum(v, 2) },
                { key: 'last', label: 'Last', align: 'right',
                  thClass: 'ac-th-last', cellClass: 'ac-last',
                  format: v => fmtNum(v, 2) },
                { key: 'change', label: 'Chng', align: 'right',
                  cellClass: v => 'ac-num ' + (v > 0 ? 'ac-pos' : v < 0 ? 'ac-neg' : ''),
                  format: v => v !== 0 ? (v > 0 ? '+' : '') + fmtNum(Math.abs(v), 2) : '—' },
                { key: 'pct_change', label: '%Chng', align: 'right',
                  cellClass: v => 'ac-num ' + (v > 0 ? 'ac-pos' : v < 0 ? 'ac-neg' : ''),
                  format: v => v !== 0 ? (v > 0 ? '+' : '') + fmtNum(Math.abs(v), 2) + '%' : '—' },
                { key: 'volume', label: 'Volume (Contracts)', align: 'right',
                  cellClass: 'ac-volume', format: v => fmtVol(v) },
            ],
        });
    }

    // ── Helpers ────────────────────────────────────────────────────
    function fmtNum(n, dec) {
        if (n == null || n === 0) return '—';
        return Number(n).toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }

    function fmtVol(n) {
        if (!n) return '—';
        return Number(n).toLocaleString('en-IN');
    }

    function fmtDate(iso) {
        if (!iso) return '—';
        const d = new Date(iso + 'T00:00:00');
        return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
                .replace(/ /g, '-');
    }

    function esc(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

})();
