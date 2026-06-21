(function () {
    'use strict';

    let _underlying  = 'NIFTY';
    let _expiry      = '';
    let _optionType  = 'all';
    let _strike      = '';
    let _data        = null;
    let _wired       = false;

    const $ = id => document.getElementById(id);

    // ── Public init called by dashSwitch ──────────────────────────
    window.acInit = function () {
        wire();
        if (!window._acLoaded) fetchContracts();
    };

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
    function fetchContracts() {
        $('acLoading').classList.remove('hidden');
        $('acTableWrap').classList.add('hidden');
        $('acError').classList.add('hidden');
        $('acStatTotal').textContent = '';

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
                $('acTableWrap').classList.remove('hidden');
                render();
            })
            .catch(err => {
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

        if (!total) {
            $('acTableBody').innerHTML = '';
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

        const rows = contracts.map((c, idx) => {
            const chgClass  = c.change  > 0 ? 'ac-pos' : c.change  < 0 ? 'ac-neg' : '';
            const pctClass  = c.pct_change > 0 ? 'ac-pos' : c.pct_change < 0 ? 'ac-neg' : '';
            const chgSign   = c.change > 0 ? '+' : '';
            const pctSign   = c.pct_change > 0 ? '+' : '';
            const strikeTxt = c.strike != null ? fmtNum(c.strike, 2) : '—';
            const expiryTxt = fmtDate(c.expiry);
            const optTxt    = c.option_type || '—';
            const isTop     = idx < 6;
            const topClass  = isTop ? 'ac-top-row' : '';

            const isTopCe    = isTop && optTxt === 'CE' && c.strike === topCeStrike;
            const isTopPe    = isTop && optTxt === 'PE' && c.strike === topPeStrike;
            const strikeClass = isTopCe ? 'ac-strike-ce' : isTopPe ? 'ac-strike-pe' : '';
            const highClass   = (isTopCe || isTopPe) ? 'ac-high ac-high-bold' : 'ac-high';
            const isStraddle  = isTop && c.strike != null && straddleStrikes.has(c.strike);
            const strikeInner = isStraddle
                ? `<span class="ac-straddle-box ${strikeClass}">${strikeTxt}</span>`
                : `<span class="${strikeClass}">${strikeTxt}</span>`;

            return `<tr class="${topClass}">
<td class="ac-info-cell"><span class="ac-info-icon" title="${esc(c.symbol)}">ℹ</span></td>
<td class="ac-inst-type">${esc(c.instrument_type)}</td>
<td class="ac-expiry-col">${expiryTxt}</td>
<td>${optTxt !== '—' ? '<span class="ac-type-badge ' + optTxt.toLowerCase() + '">' + optTxt + '</span>' : '<span class="ac-dash">—</span>'}</td>
<td class="col-r ac-strike">${strikeInner}</td>
<td class="col-r ac-num">${fmtNum(c.open, 2)}</td>
<td class="col-r ac-num ${highClass}">${fmtNum(c.high, 2)}</td>
<td class="col-r ac-num ac-low">${fmtNum(c.low, 2)}</td>
<td class="col-r ac-num">${fmtNum(c.close, 2)}</td>
<td class="col-r ac-num">${fmtNum(c.prev_close, 2)}</td>
<td class="col-r ac-last">${fmtNum(c.last, 2)}</td>
<td class="col-r ac-num ${chgClass}">${c.change !== 0 ? chgSign + fmtNum(Math.abs(c.change), 2) : '—'}</td>
<td class="col-r ac-num ${pctClass}">${c.pct_change !== 0 ? pctSign + fmtNum(Math.abs(c.pct_change), 2) + '%' : '—'}</td>
<td class="col-r ac-volume">${fmtVol(c.volume)}</td>
</tr>`;
        });

        $('acTableBody').innerHTML = rows.join('');
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
