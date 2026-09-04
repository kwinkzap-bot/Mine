// Historic OI page logic (extracted from dashboard.html)
// ── Historic OI ────────────────────────────────────────────────
const _HOI_SYMBOLS = ['NIFTY'];

// Previous completed session's Avg 3 VWAP per symbol — set by _hoiRenderAll,
// read by the live badge (fresh close-values, so we don't recompute it from
// the grid on every tick).
const _hoiPrevAvg3Vwap = {};

function _hoiFmt(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString('en-IN');
}

function _hoiChngCls(v) {
    return v > 0 ? 'hoi-up' : v < 0 ? 'hoi-down' : 'hoi-flat';
}

// Shared grid (DataGrid). Deliberately NOT sortable: "FII Chng Fut OI" is a
// day-over-day delta computed against the NEXT array row (the list arrives
// newest-first), so reordering rows would silently change its values.
function _hoiRenderAll(records) {

    _HOI_SYMBOLS.forEach(sym => {
        const grid = document.getElementById('hoiGrid-' + sym);
        if (!grid) return;
        const rows = (records || []).filter(r => r.symbol === sym);
        if (rows.length === 0) {
            grid.innerHTML = `<div class="dg-empty">No records for ${sym}. Click ⏺ Record to capture today's OI.</div>`;
            return;
        }
        const _today = new Date().toLocaleDateString('sv'); // YYYY-MM-DD in local time
        const _DAY = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        const dayOf   = r => _DAY[new Date(r.date + 'T00:00:00').getDay()];
        const isMon   = r => dayOf(r) === 'Mon';
        const hasOHLC = r => Number(r.close || 0) > 0;
        const fmtP = (r, n) => hasOHLC(r) && Number(n)
            ? Number(n).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
        const fmtVwap = r => r.vwap != null && Number(r.vwap) > 0
            ? Number(r.vwap).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
        // 3-day average VWAP = this row's VWAP plus the two PREVIOUS sessions'
        // (rows[i+1], rows[i+2] — the grid is newest-first, same convention as
        // chngFutOf below). null until 3 consecutive VWAP values are available.
        const avg3VwapOf = (i) => {
            const a = rows[i]?.vwap, b = rows[i + 1]?.vwap, c = rows[i + 2]?.vwap;
            if (!(a > 0) || !(b > 0) || !(c > 0)) return null;
            return (Number(a) + Number(b) + Number(c)) / 3;
        };
        // Previous session's Avg 3 VWAP — one row further back (newest-first grid).
        const prevAvg3VwapOf = (i) => avg3VwapOf(i + 1);
        // Baseline for the live badge: the last FULLY COMPLETED session's Avg 3
        // VWAP. Skip row 0 if it's already today's (partially-formed) record so
        // "previous session" means the same thing whether or not today has been
        // recorded yet.
        const todayIso = new Date().toLocaleDateString('sv');
        const baseIdx  = (rows[0] && rows[0].date === todayIso) ? 1 : 0;
        _hoiPrevAvg3Vwap[sym] = avg3VwapOf(baseIdx);
        // Monday's High/Low get the accent box — the week-open range the case
        // studies key off.
        const monBox = (r, txt) => isMon(r) ? `<span class="hoi-mon-box">${txt}</span>` : txt;
        const diffOf = r => Number(r.pe_oi) - Number(r.ce_oi);
        const pcrOf  = r => Number(r.ce_oi) > 0 ? Number(r.pe_oi) / Number(r.ce_oi) : null;
        // "Chng Fut OI" = day-over-day change in FII net index-futures OI
        // (FII Future Index Long − Short, from NSE participant-wise OI).
        // rows[i+1] is the PREVIOUS session — the grid is newest-first.
        const chngFutOf = (r, i) => {
            const cur  = r.fii_fut_oi != null ? Number(r.fii_fut_oi) : null;
            const prev = rows[i + 1]?.fii_fut_oi != null ? Number(rows[i + 1].fii_fut_oi) : null;
            return (cur != null && prev != null) ? cur - prev : null;
        };
        const futOf = r => (r.FII_Index_futures != null && r.FII_Index_futures !== 0)
            ? Number(r.FII_Index_futures) : null;
        const cls = v => v == null ? 'hoi-flat' : v > 0 ? 'hoi-up' : v < 0 ? 'hoi-down' : 'hoi-flat';
        // Day-over-day change in total CE/PE OI. rows[i+1] is the PREVIOUS
        // session — the grid is newest-first (same convention as chngFutOf).
        const chngCeOf = (r, i) => {
            const cur = r.ce_oi != null ? Number(r.ce_oi) : null;
            const prev = rows[i + 1]?.ce_oi != null ? Number(rows[i + 1].ce_oi) : null;
            return (cur != null && prev != null) ? cur - prev : null;
        };
        const chngPeOf = (r, i) => {
            const cur = r.pe_oi != null ? Number(r.pe_oi) : null;
            const prev = rows[i + 1]?.pe_oi != null ? Number(rows[i + 1].pe_oi) : null;
            return (cur != null && prev != null) ? cur - prev : null;
        };

        grid.innerHTML = DataGrid.render({
            rows,
            rowClass: r => isMon(r) ? 'hoi-mon-row' : '',
            columns: [
                { key: 'date', label: 'Date', strong: true },
                { label: 'Day', align: 'center', render: (_, r) => isMon(r)
                    ? '<span class="hoi-day-mon">Mon</span>'
                    : `<span style="color:var(--pf-text-2);font-size:11px;font-weight:600">${dayOf(r)}</span>` },
                { label: 'CHNG OPT OI', align: 'right',
                  cellClass: (_, r) => cls(diffOf(r)),
                  render: (_, r) => {
                      const d = diffOf(r);
                      return (d > 0 ? '↑ ' : d < 0 ? '↓ ' : '') + _hoiFmt(Math.abs(d));
                  } },
                { label: 'CHNG CALL OI', align: 'right',
                  cellClass: (_, r, i) => cls(chngCeOf(r, i)),
                  format: (_, r, i) => {
                      const d = chngCeOf(r, i);
                      return d == null ? '—' : (d > 0 ? '+' : '-') + _hoiFmt(Math.abs(d));
                  } },
                { label: 'CHNG PUT OI', align: 'right',
                  cellClass: (_, r, i) => cls(chngPeOf(r, i)),
                  format: (_, r, i) => {
                      const d = chngPeOf(r, i);
                      return d == null ? '—' : (d > 0 ? '+' : '-') + _hoiFmt(Math.abs(d));
                  } },
                { label: 'PCR', align: 'right',
                  cellClass: (_, r) => { const p = pcrOf(r); return p == null ? 'hoi-flat' : p >= 1 ? 'hoi-up' : 'hoi-down'; },
                  format: (_, r) => { const p = pcrOf(r); return p == null ? '—' : p.toFixed(2); } },
                { label: 'FII Chng Fut OI', align: 'right',
                  cellClass: (_, r, i) => cls(chngFutOf(r, i)),
                  format: (_, r, i) => {
                      const d = chngFutOf(r, i);
                      return d == null ? '—' : (d > 0 ? '+' : '-') + _hoiFmt(Math.abs(d));
                  } },
                { label: 'FII Fut', align: 'right',
                  cellClass: (_, r) => cls(futOf(r)),
                  format: (_, r) => { const f = futOf(r); return f == null ? '—' : (f > 0 ? '+' : '') + f.toFixed(0) + ' Cr'; } },
                { label: 'Open',  align: 'right',
                  thTitle: 'Cell tint: this session\u2019s Open vs. the PREVIOUS session\u2019s '
                         + 'Avg 3 VWAP (the value one row below in the Avg 3 VWAP column).\n'
                         + 'Green = Open above it (upside), red = at or below (downside).\n'
                         + 'No tint when the row has no OHLC yet, or the 3 prior sessions\u2019 '
                         + 'VWAPs aren\u2019t all available.',
                  cellClass: (_, r, i) => {
                      if (!hasOHLC(r)) return '';
                      const pa = prevAvg3VwapOf(i);
                      if (pa == null) return '';
                      return Number(r.open) > pa ? 'hoi-bg-pos' : 'hoi-bg-neg';
                  },
                  format: (_, r) => fmtP(r, r.open) },
                { label: 'High',  align: 'right', render: (_, r) => monBox(r, fmtP(r, r.high)) },
                { label: 'Low',   align: 'right', render: (_, r) => monBox(r, fmtP(r, r.low)) },
                { label: 'Close', align: 'right', format: (_, r) => fmtP(r, r.close) },
                { label: 'VWAP', align: 'right', format: (_, r) => fmtVwap(r) },
                { label: 'Avg 3 VWAP', align: 'right', format: (_, r, i) => {
                    const a = avg3VwapOf(i);
                    return a == null ? '—' : Number(a).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
                } },
                { label: 'Move', align: 'center',
                  cellClass: (_, r) => !hasOHLC(r) ? 'hoi-flat' : Number(r.close) >= Number(r.open) ? 'hoi-up' : 'hoi-down',
                  format: (_, r) => !hasOHLC(r) ? '—' : Number(r.close) >= Number(r.open) ? '▲' : '▼' },
                { label: '', align: 'center', render: (_, r) => {
                    const refresh = `<button class="hoi-refresh-btn" title="Refresh this row from all sources (OI, OHLC, Fut OI, FII)" onclick="refreshHistoricOI('${r.date}','${r.symbol}',this)">&#x21bb;</button>`;
                    const del = r.date === _today
                        ? `<button class="hoi-del-btn" title="Delete" onclick="deleteHistoricOI('${r.date}','${r.symbol}')">&#x2715;</button>`
                        : '';
                    return refresh + del;
                } },
            ],
        });
    });
}

// ── Next-session prediction panel (5-year statistical analysis) ─────────────
function _hoiRenderPrediction(d) {
    const el = document.getElementById('hoiPredict');
    if (!el) return;
    if (!d || !d.success) {
        el.innerHTML = `<span style="font-size:11px;color:var(--pf-text-3)">Prediction unavailable — ${d && d.error ? d.error : 'analysis failed'}</span>`;
        return;
    }
    const col = d.prediction === 'BULLISH' ? 'var(--pf-pos)'
              : d.prediction === 'BEARISH' ? 'var(--pf-neg)'
              : 'var(--pf-text-2)';
    const icon = d.prediction === 'BULLISH' ? '▲' : d.prediction === 'BEARISH' ? '▼' : '◆';
    const mv   = d.expected_move_pct;
    const mvTxt = (mv > 0 ? '+' : '') + mv.toFixed(2) + '%';

    // Open-vs-Prev-Avg-3-VWAP read: same comparison as the grid's Open-column
    // tint, computed server-side (analyze_and_predict) so this page and the
    // Trend page always show the same value — kept as its own chip, separate
    // from the 5-year blended signals below, since it isn't part of that model.
    const openSig = d.open_vs_prev_avg3vwap;
    const openSigChip = openSig ? (() => {
        const c   = openSig.upside ? 'var(--pf-pos)' : 'var(--pf-neg)';
        const fmt = n => Number(n).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
        return `<span class="hoi-sig-chip">
                  <span style="color:${c};font-size:9px">●</span>Open <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${fmt(openSig.open)}</b>
                  vs Prev Avg3 VWAP <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${fmt(openSig.prev_avg3_vwap)}</b>
                  <b style="color:${c};font-variant-numeric:tabular-nums">${openSig.upside ? 'Upside' : 'Downside'}</b></span>`;
    })() : '';

    // Crisp default view — one compact chip per signal (dot + short label + value)
    const chips = (d.reasons || []).map(r => {
        const short = r.name.split('(')[0].trim();
        const c = r.bullish ? 'var(--pf-pos)' : 'var(--pf-neg)';
        return `<span class="hoi-sig-chip"><span style="color:${c};font-size:9px">●</span>${short}
                  <b style="color:var(--pf-text-1);font-variant-numeric:tabular-nums">${r.value}</b></span>`;
    }).join('');

    // Full breakdown — revealed on info-icon hover
    const rows = (d.reasons || []).map(r => `
        <div style="display:flex;gap:8px;align-items:baseline;padding:3px 0;">
          <span style="color:${r.bullish ? 'var(--pf-pos)' : 'var(--pf-neg)'};font-size:10px">●</span>
          <span style="font-size:11.5px;color:var(--pf-text-1);font-weight:600;white-space:nowrap">${r.name}: ${r.value}</span>
          <span style="font-size:11px;color:var(--pf-text-2)">${r.bucket} — ${r.why}.
            <b class="${r.prob_up_pct >= d.base_rate_pct ? 'hoi-up' : 'hoi-down'}">${r.prob_up_pct}%</b>
            of similar days closed higher next session
            (avg ${r.avg_ret_pct > 0 ? '+' : ''}${r.avg_ret_pct}%, n=${r.n})</span>
        </div>`).join('');
    const popup = `
      <div style="font-size:11px;font-weight:700;color:var(--pf-text-2);margin-bottom:6px">
        Signal breakdown · up-probability <b style="color:${col}">${d.prob_up_pct}%</b> vs 5-yr base rate ${d.base_rate_pct}%
      </div>
      ${rows}
      <div style="font-size:10px;color:var(--pf-text-3);margin-top:8px;border-top:1px solid var(--pf-border-sub);padding-top:6px">
        model hit-rate ${d.backtest_hit_pct != null ? d.backtest_hit_pct + '%' : '—'} over ${d.sample_days.toLocaleString('en-IN')} days (${d.from_date} → ${d.to_date}).
        Statistical tendencies from this grid's own 5-year history (in-sample) — not financial advice.
      </div>`;

    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:11px;font-weight:700;letter-spacing:.05em;color:var(--pf-text-3)">NEXT SESSION OUTLOOK · ${d.next_session}</span>
        <span style="font-size:15px;font-weight:800;color:${col}">${icon} ${d.prediction}</span>
        <span style="font-size:11.5px;color:var(--pf-text-2)">
          <b style="color:${col}">${d.prob_up_pct}%</b> up · move <b style="color:${col}">${mvTxt}</b>
        </span>
        <span class="hoi-info-wrap" style="margin-left:auto">
          <span class="hoi-info-btn" tabindex="0" aria-label="Show full signal breakdown">i</span>
          <div class="hoi-info-pop">${popup}</div>
        </span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${openSigChip}${chips}</div>`;
}

function loadHoiPrediction() {
    fetch('/api/oi-historic/predict')
        .then(r => r.json())
        .then(_hoiRenderPrediction)
        .catch(() => _hoiRenderPrediction(null));
}

// ── Live badge — NIFTY LTP vs. previous session's Avg 3 VWAP ────────────────
// Only meaningful while the market is open: outside those hours there's no
// live tick to compare, so the badge just hides itself.
let _hoiLiveTimer = null;

function _hoiIsMarketOpen() {
    // Compute in IST regardless of the viewer's own timezone.
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Kolkata', weekday: 'short', hour: 'numeric',
        minute: 'numeric', hour12: false,
    }).formatToParts(new Date());
    const get = t => parts.find(p => p.type === t)?.value;
    const weekday = get('weekday');
    if (weekday === 'Sat' || weekday === 'Sun') return false;
    const mins = Number(get('hour')) * 60 + Number(get('minute'));
    return mins >= (9 * 60 + 15) && mins <= (15 * 60 + 30);
}

function _hoiUpdateLiveBadge() {
    const el = document.getElementById('hoiLiveBadge');
    if (!el) return;

    if (!_hoiIsMarketOpen()) {
        el.style.display = 'none';
        if (_hoiLiveTimer) { clearInterval(_hoiLiveTimer); _hoiLiveTimer = null; }
        return;
    }

    const prevAvg3 = _hoiPrevAvg3Vwap['NIFTY'];
    if (prevAvg3 == null) { el.style.display = 'none'; return; }

    fetch('/api/underlying-price?symbol=NIFTY&price_source=ltp')
        .then(r => r.json())
        .then(d => {
            if (!d.success || !d.ltp) { el.style.display = 'none'; return; }
            const ltp = Number(d.ltp);
            const up  = ltp > prevAvg3;
            const cls = up ? 'hoi-up' : 'hoi-down';
            const fmt = n => Number(n).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
            el.style.display = '';
            el.className = 'hoi-live-badge ' + (up ? 'hoi-bg-pos' : 'hoi-bg-neg');
            el.innerHTML = `
                <span class="hoi-live-dot ${cls}"></span>
                <span class="${cls}" style="text-transform:uppercase;letter-spacing:.04em;font-size:10px">Live</span>
                <span>NIFTY <b class="${cls}">${fmt(ltp)}</b> ${up ? '▲' : '▼'} prev Avg 3 VWAP <b>${fmt(prevAvg3)}</b></span>`;
        })
        .catch(() => { el.style.display = 'none'; });
}

function _hoiStartLiveBadge() {
    _hoiUpdateLiveBadge();
    if (_hoiLiveTimer) clearInterval(_hoiLiveTimer);
    _hoiLiveTimer = setInterval(_hoiUpdateLiveBadge, 15000);
}

function loadHistoricOI() {
    return fetch('/api/oi-historic')
        .then(r => r.json())
        .then(d => { if (d.success) { _hoiRenderAll(d.records); _hoiStartLiveBadge(); } })
        .catch(() => {
            _HOI_SYMBOLS.forEach(s => {
                const grid = document.getElementById('hoiGrid-' + s);
                if (grid) grid.innerHTML = '<div class="dg-empty hoi-down">Failed to load records.</div>';
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
                _hoiUpdateLiveBadge();
                loadHoiPrediction();
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

document.addEventListener("DOMContentLoaded", () => {
    loadHistoricOI().then(loadHoiPrediction);
});
