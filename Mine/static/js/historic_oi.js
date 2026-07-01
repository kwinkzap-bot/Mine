// Historic OI page logic (extracted from dashboard.html)
// ── Historic OI ────────────────────────────────────────────────
const _HOI_SYMBOLS = ['NIFTY'];

function _hoiFmt(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString('en-IN');
}

function _hoiChngCls(v) {
    return v > 0 ? 'hoi-up' : v < 0 ? 'hoi-down' : 'hoi-flat';
}

function _hoiRenderAll(records) {

    _HOI_SYMBOLS.forEach(sym => {
        const tbody = document.getElementById('hoiTbody-' + sym);
        const rows = (records || []).filter(r => r.symbol === sym);
        if (rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="12" class="sw-empty">No records for ${sym}. Click ⏺ Record to capture today's OI.</td></tr>`;
            return;
        }
        const _today = new Date().toLocaleDateString('sv'); // YYYY-MM-DD in local time
        tbody.innerHTML = rows.map((r, idx) => {
            const cv   = Number(r.chng_ce_oi), pv = Number(r.chng_pe_oi);
            const ceOI = Number(r.ce_oi), peOI = Number(r.pe_oi);
            const diff = peOI - ceOI;
            const pcr  = ceOI > 0 ? (peOI / ceOI).toFixed(2) : '—';
            const pcrCls = ceOI > 0
                ? (peOI / ceOI >= 1 ? 'hoi-up' : 'hoi-down')
                : 'hoi-flat';
            const cSign = cv > 0 ? '+' : '', pSign = pv > 0 ? '+' : '';
            const diffArrow = diff > 0 ? '↑ ' : diff < 0 ? '↓ ' : '';
            const diffCls   = diff > 0 ? 'hoi-up' : diff < 0 ? 'hoi-down' : 'hoi-flat';
            const o = Number(r.open || 0), h = Number(r.high || 0),
                  l = Number(r.low  || 0), c = Number(r.close || 0);
            const hasOHLC  = c > 0;
            const moveUp   = c >= o;
            const moveCls  = !hasOHLC ? 'hoi-flat' : moveUp ? 'hoi-up' : 'hoi-down';
            const moveText = !hasOHLC ? '—' : moveUp ? '▲' : '▼';
            const fmtP = n => hasOHLC && n ? n.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
            const refreshBtn = `<button class="hoi-refresh-btn" title="Refresh this row from all sources (OI, OHLC, Fut OI, FII)" onclick="refreshHistoricOI('${r.date}','${r.symbol}',this)">&#x21bb;</button>`;
            const delBtn = r.date === _today
                ? `<button class="hoi-del-btn" title="Delete" onclick="deleteHistoricOI('${r.date}','${r.symbol}')">&#x2715;</button>`
                : '';
            const delCell = `${refreshBtn}${delBtn}`;
            const _DAY = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
            const _day = _DAY[new Date(r.date + 'T00:00:00').getDay()];
            const isMon = _day === 'Mon';
            const dayCell = isMon ? '<span class="hoi-day-mon">Mon</span>' : `<span style="color:var(--pf-text-2);font-size:11px;font-weight:600">${_day}</span>`;
            const monBox  = txt => isMon ? `<span class="hoi-mon-box">${txt}</span>` : txt;
            const prevRec    = rows[idx + 1];
            // "Chng Fut OI" = day-over-day change in FII net index-futures OI
            // (FII Future Index Long − Short, from NSE participant-wise OI).
            const curFut     = r.fii_fut_oi != null ? Number(r.fii_fut_oi) : null;
            const prevFut    = prevRec?.fii_fut_oi != null ? Number(prevRec.fii_fut_oi) : null;
            const chngFut    = (curFut != null && prevFut != null) ? curFut - prevFut : null;
            const chngFutCls = chngFut == null ? 'hoi-flat' : chngFut > 0 ? 'hoi-up' : 'hoi-down';
            const chngFutTxt = chngFut == null ? '—' : (chngFut > 0 ? '+' : '-') + _hoiFmt(Math.abs(chngFut));
            const _fiiRaw = r.FII_Index_futures;
            const futOI  = (_fiiRaw != null && _fiiRaw !== 0) ? Number(_fiiRaw) : null;
            const fiiCls = futOI == null ? 'hoi-flat' : futOI > 0 ? 'hoi-up' : 'hoi-down';
            const fiiTxt = futOI == null ? '—' : (futOI > 0 ? '+' : '') + futOI.toFixed(0) + ' Cr';
            return `<tr${isMon ? ' class="hoi-mon-row"' : ''}>
              <td class="sw-td-l">${r.date}</td>
              <td class="sw-td-c">${dayCell}</td>
              <td class="${diffCls}">${diffArrow}${_hoiFmt(Math.abs(diff))}</td>
              <td class="${pcrCls}">${pcr}</td>
              <td class="${chngFutCls}">${chngFutTxt}</td>
              <td class="${fiiCls}">${fiiTxt}</td>
              <td>${fmtP(o)}</td>
              <td>${monBox(fmtP(h))}</td>
              <td>${monBox(fmtP(l))}</td>
              <td>${fmtP(c)}</td>
              <td class="sw-td-c ${moveCls}">${moveText}</td>
              <td class="sw-td-c">${delCell}</td>
            </tr>`;
        }).join('');
    });
}

function loadHistoricOI() {
    fetch('/api/oi-historic')
        .then(r => r.json())
        .then(d => { if (d.success) _hoiRenderAll(d.records); })
        .catch(() => {
            _HOI_SYMBOLS.forEach(s => {
                document.getElementById('hoiTbody-' + s).innerHTML =
                    '<tr><td colspan="12" class="sw-empty hoi-down">Failed to load records.</td></tr>';
            });
        });
}

// Fetch Latest — record today's snapshot for all symbols (OI/OHLC/Fut OI) and
// sync the FII flow window in one call. Same action the 8:00 PM IST job runs.
function hoiFetchLatest() {
    const btn = document.getElementById('hoiFetchLatestBtn');
    const status = document.getElementById('hoiStatus');
    btn.disabled = true;
    btn.textContent = '⏳ Fetching…';
    status.textContent = '';
    fetch('/api/oi-historic/record', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            btn.disabled = false;
            btn.textContent = '⟳ Fetch Latest';
            if (d.success) {
                _hoiRenderAll(d.records);
                const errs = (d.errors || []).filter(e => !e.success).map(e => e.symbol).join(', ');
                status.textContent = errs ? `⚠️ Errors: ${errs}` : '✅ Latest saved';
            } else {
                status.textContent = '❌ ' + (d.error || 'Failed');
            }
        })
        .catch(() => {
            btn.disabled = false;
            btn.textContent = '⟳ Fetch Latest';
            status.textContent = '❌ Network error';
        });
}

function deleteHistoricOI(date, symbol) {
    if (!confirm(`Delete OI record for ${symbol} on ${date}?`)) return;
    fetch(`/api/oi-historic/${date}/${symbol}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(d => { if (d.success) loadHistoricOI(); })
        .catch(() => alert('Delete failed'));
}

// Refresh a single row from every source (bhavcopy OI/OHLC/Fut OI + FII flow)
// in one call — replaces the old Record → Sync FII → Recalculate button dance.
function refreshHistoricOI(date, symbol, btn) {
    const status = document.getElementById('hoiStatus');
    btn.disabled = true;
    btn.innerHTML = '⏳';
    status.textContent = `Refreshing ${symbol} ${date}…`;
    fetch('/api/oi-historic/refresh', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ date, symbol }),
    })
    .then(r => r.json())
    .then(d => {
        if (d.success) {
            if (d.records) _hoiRenderAll(d.records); else loadHistoricOI();
            status.textContent = `✅ Refreshed ${symbol} ${date}`;
        } else {
            btn.disabled = false;
            btn.innerHTML = '↻';
            status.textContent = '❌ ' + (d.error || 'Refresh failed');
        }
    })
    .catch(() => {
        btn.disabled = false;
        btn.innerHTML = '↻';
        status.textContent = '❌ Network error';
    });
}

// ── Historic OI — Historical Update (date-range backfill) ───────
let _hoiPollTimer = null;

function hoiToggleHistorical() {
    const panel = document.getElementById('hoiHistoricalPanel');
    const showing = panel.style.display !== 'none';
    panel.style.display = showing ? 'none' : 'flex';
    if (!showing) {
        // Default "to" date to today
        const today = new Date().toLocaleDateString('sv');
        document.getElementById('hoiToDate').value = today;
    }
}

// Historical Update — backfill every trading day in [from, to] from NSE
// bhavcopy (OI/OHLC/Fut OI), then sync the FII flow window server-side.
function hoiHistoricalUpdate() {
    const fromDate = document.getElementById('hoiFromDate').value;
    const toDate   = document.getElementById('hoiToDate').value;
    const updateBtn = document.getElementById('hoiUpdateBtn');
    const msg      = document.getElementById('hoiLoadMsg');

    if (!fromDate || !toDate) { msg.textContent = '⚠ Select date range'; return; }
    if (fromDate > toDate)    { msg.textContent = '⚠ From must be ≤ To'; return; }

    updateBtn.disabled = true;
    msg.textContent  = 'Starting…';
    document.getElementById('hoiProgressWrap').style.display = '';

    fetch('/api/oi-historic/load-all', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ source: 'nse', from_date: fromDate, to_date: toDate }),
    })
    .then(r => r.json())
    .then(d => {
        if (d.success) {
            msg.textContent = 'Running…';
            _hoiPollBackfill();
        } else {
            updateBtn.disabled = false;
            msg.textContent  = '❌ ' + (d.error || 'Failed to start');
            document.getElementById('hoiProgressWrap').style.display = 'none';
        }
    })
    .catch(() => {
        updateBtn.disabled = false;
        msg.textContent  = '❌ Network error';
        document.getElementById('hoiProgressWrap').style.display = 'none';
    });
}

function _hoiPollBackfill() {
    if (_hoiPollTimer) clearTimeout(_hoiPollTimer);
    fetch('/api/oi-historic/load-status')
        .then(r => r.json())
        .then(st => {
            const pct = st.total > 0 ? Math.round(st.progress / st.total * 100) : 0;
            document.getElementById('hoiProgressFill').style.width = pct + '%';
            document.getElementById('hoiProgressPct').textContent  = pct + '%';
            document.getElementById('hoiProgressMsg').textContent  = st.message || '';
            if (st.running) {
                _hoiPollTimer = setTimeout(_hoiPollBackfill, 3000);
            } else {
                document.getElementById('hoiUpdateBtn').disabled  = false;
                document.getElementById('hoiLoadMsg').textContent = st.message || 'Done';
                loadHistoricOI();
            }
        })
        .catch(() => { _hoiPollTimer = setTimeout(_hoiPollBackfill, 5000); });
}

document.addEventListener("DOMContentLoaded", loadHistoricOI);
