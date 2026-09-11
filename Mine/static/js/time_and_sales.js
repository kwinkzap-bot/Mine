/* Time and Sales — the live trade tape.
 *
 * Polls /api/time-and-sales once a second. That endpoint reads an in-memory
 * store filled by a websocket on the server, so this poll costs the broker
 * nothing however many tabs are open — unlike every other live panel here,
 * where the poll interval IS the broker request rate.
 *
 * The feed is throttled upstream, so the tape is a true but incomplete record:
 * every row is a real exchange print, and a good deal of volume trades between
 * the snapshots we are shown. The feed stat states that outright rather than
 * letting the tape imply it is the whole story.
 */

const TAS_POLL_MS        = 1000;
const TAS_POLL_MS_HIDDEN = 10000;
const TAS_POLL_MS_CLOSED = 300000;
const TAS_POLL_MS_ERROR  = 3000;

// The whole session is fetched in one go — it is ~850KB over loopback, where
// that costs milliseconds, and paging it would buy nothing but round-trips and
// a spinner halfway down a scroll. The cap matches the server store's own.
const TAS_MAX_ROWS  = 25000;
// The DOM is where the cost actually is: repainting thousands of rows on every
// print is what would feel slow. So only a window is painted, and it grows as
// the reader scrolls into it.
const TAS_DOM_ROWS  = 300;
const TAS_DOM_STEP  = 300;
// Grow this far before the reader reaches the bottom, so the next rows are
// already there rather than appearing under them.
const TAS_GROW_SLACK = 400;

let _tasRows    = [];
let _tasCursor  = 0;
let _tasSymbol  = null;
let _tasTimer   = null;
let _tasLoading = false;
let _tasPending = false;
let _tasMinQty  = 0;
let _tasState   = {};
let _tasPainted = false;
let _tasWindow  = TAS_DOM_ROWS;
// Which revision of the tape our cursor belongs to. History keeps catching up
// over the live prints all session, and each catch-up renumbers every seq past
// the seam it filled — so the cursor is only meaningful paired with this.
let _tasEpoch   = null;

const _tasFmt = n => Number(n).toLocaleString('en-IN');
const _tasPrice = n => Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: 2, maximumFractionDigits: 2 });

function _tasTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString('en-GB', {
        hour12: false, timeZone: 'Asia/Kolkata' });
}

/* "NSE:NIFTY26SEPFUT" is what the API speaks; a human picking a contract
   wants the month. list_future_contracts gives us the real expiry date, so
   build the label from that rather than parsing the symbol string. */
function _tasContractLabel(c) {
    if (c.label) return c.label;
    if (c.expiry) {
        const d = new Date(c.expiry);
        // The full expiry date, not just the month: two contracts can share a
        // month around a roll, and the day is what tells them apart.
        if (!isNaN(d)) {
            return d.toLocaleDateString('en-GB',
                { day: '2-digit', month: 'short', year: 'numeric' });
        }
    }
    return c.symbol;
}

const _tasColumns = [
    { key: 'ts', label: 'Time', format: _tasTime, cellClass: 'tas-c-time' },
    {
        key: 'price', label: 'Price', align: 'right', strong: true,
        format: _tasPrice,
        tone: (v, r) => (r.side === 'buy' ? 'pos' : r.side === 'sell' ? 'neg' : 'muted'),
        title: (v, r) => `Aggressor: ${r.side} (${r.side_rule} rule)`,
        cellClass: 'tas-c-price',
    },
    {
        key: 'qty', label: 'Qty', align: 'right',
        // A backfilled row is one 1-second bar, i.e. every trade in that
        // second added together — a different quantity from a single print.
        // The sigma says so rather than letting it pass as one trade.
        format: (v, r) => (v == null ? '—' : (r.src === 'bar' ? 'Σ ' + _tasFmt(v) : _tasFmt(v))),
        cellClass: (v, r) => 'tas-c-qty' + (r.src === 'bar' ? ' tas-agg' : ''),
        title: (v, r) => (r.src === 'bar'
            ? 'Aggregated: the total quantity traded in this 1-second bar, not a single '
              + 'trade. Backfilled from ICICI 1-second history, where per-trade size is '
              + 'not available.'
            : 'Exchange last-traded quantity — the size of this single trade.'),
    },
];

function _tasRenderRail() {
    const el = document.getElementById('tasRail');
    if (!el) return;

    // Every figure here comes from the server, computed over the whole tape.
    // Deriving them from _tasRows meant describing the client's capped window:
    // the print count just reported the cap, and the flow split described the
    // last few hundred trades rather than the session.
    const st = _tasState;
    const flow = { buy: st.flow_buy || 0, sell: st.flow_sell || 0 };
    flow.total = flow.buy + flow.sell;
    const pct = flow.total ? Math.round((flow.buy / flow.total) * 100) : null;
    const cov = st.coverage;

    const tile = (label, value, cls) =>
        `<div class="tas-tile"><span class="tas-tile-k">${label}</span>` +
        `<span class="tas-tile-v ${cls || ''}">${value}</span></div>`;

    const lastCls = st.last_side === 'buy' ? 'dg-pos' : st.last_side === 'sell' ? 'dg-neg' : '';

    el.innerHTML =
        `<div class="dg-card tas-rail-card">
           <div class="dg-card-hdr"><span class="dg-card-title">Session</span></div>
           <div class="tas-tiles">
             ${tile('Last', st.last_price != null ? _tasPrice(st.last_price) : '—', lastCls)}
             ${tile('Prints', st.prints != null ? _tasFmt(st.prints) : '—')}
             ${tile('Feed', cov != null ? Math.round(cov * 100) + '%' : '—')}
           </div>
           <div class="tas-flow">
             <div class="tas-flow-hdr">
               <span>Order flow</span>
               <span class="tas-flow-pct">${pct != null ? pct + '% buy' : '—'}</span>
             </div>
             <div class="tas-flow-bar" role="img"
                  aria-label="${pct != null ? pct + '% of attributed volume was buyers' : 'no data'}">
               <span class="tas-flow-buy" style="width:${pct != null ? pct : 50}%"></span>
             </div>
             <div class="tas-flow-legend">
               <span class="dg-pos">▲ ${_tasFmt(flow.buy)}</span>
               <span class="dg-neg">▼ ${_tasFmt(flow.sell)}</span>
             </div>
           </div>
           <p class="tas-rail-note">
             Dimmed <b>Σ</b> rows are 1-second bars — one row per second, quantity is
             every trade in that second combined. History catches up all session, so
             the tape settles into Σ rows behind the last minute or so; the bright rows
             above the rule are single live prints, the only thing that can answer for
             seconds history has not reached yet.
             <b>Feed</b> is the share of traded volume those live prints pin to an
             actual trade — the broker throttles the stream, so the rest went through
             between the snapshots we are sent, which is why a print's quantity is
             never comparable with a Σ row's.
           </p>
         </div>`;
}

/* An empty tape has three quite different causes, and "Waiting for the first
   trade…" was wrong for two of them. A filter above the largest trade of the
   day is the common one, and the useful answer is what the largest actually
   was — a single exchange print is a few lots, so only an aggregated Σ bar
   ever reaches the thousands. */
function _tasEmptyMessage() {
    if (!_tasSymbol) return 'Pick a contract to start.';
    if (!_tasMinQty) return 'Waiting for the first trade…';
    const st = _tasState;
    const tick = st.max_tick_qty || 0;
    const bar = st.max_bar_qty || 0;
    if (!tick && !bar) return 'Waiting for the first trade…';
    return `No rows at or above ${_tasFmt(_tasMinQty)}. `
         + `Today's largest single print is ${_tasFmt(tick)}; the largest `
         + `1-second bar is ${_tasFmt(bar)}. Individual trades run a few lots, `
         + `so a threshold this high can only match aggregated Σ rows.`;
}

function _tasRenderTape(opts) {
    // Growing the window appends OLDER rows at the bottom, which leaves
    // everything above untouched — so the scroll offset must stay exactly
    // where it is. New prints arrive at the top and do shift the view, and
    // those are the ones that need compensating for.
    const appendedBelow = !!(opts && opts.appendedBelow);
    const grid = document.getElementById('tasGrid');
    // Rows arrive already filtered — the server searches the whole tape, which
    // the browser cannot do from its capped window.
    const shown = _tasRows.slice(-_tasWindow).reverse();     // newest first, as a tape reads

    // Repainting replaces the scroll container, and a fresh element starts at
    // scrollTop 0 — which is what yanked the reader back to the top every
    // second while they were trying to read older prints. Remember where they
    // were before the DOM goes away.
    const before = grid.querySelector('.dg-scroll');
    const prevTop = before ? before.scrollTop : 0;
    const prevHeight = before ? before.scrollHeight : 0;
    // Sitting at the top means "following the tape" — new prints should keep
    // arriving in view. Anywhere else means they are reading, and the rows
    // under their eyes must not move.
    const following = prevTop <= 4;

    // Rows come newest-first, and history keeps catching up from below — so
    // the seam sits near the TOP: above it are the live prints running ahead
    // of the frontier, below it the Σ bars that have settled. Mark it, because
    // the two are different measurements sharing a Qty column.
    const firstBar = shown.findIndex(r => r.src === 'bar');

    // Say how much of the tape is on screen, so a partial view never reads as
    // the whole session.
    const meta = _tasSymbol
        ? (shown.length < _tasRows.length
              ? `${_tasFmt(shown.length)} of ${_tasFmt(_tasRows.length)} rows`
              : `${_tasFmt(_tasRows.length)} rows`)
          + (_tasMinQty ? ` · ≥ ${_tasFmt(_tasMinQty)}` : '')
        : '';

    grid.innerHTML = DataGrid.card({
        title: 'Trades',
        meta,
        columns: _tasColumns,
        rows: shown,
        empty: _tasEmptyMessage(),
        rowClass: (r, i) => [
            r.src === 'bar' ? 'tas-row-bar' : '',
            i === firstBar && firstBar > 0 ? 'tas-row-histstart' : '',
            i === 0 && r.src === 'tick' ? 'tas-row-new' : '',
        ].filter(Boolean).join(' '),
    });

    const after = grid.querySelector('.dg-scroll');
    if (after && !following) {
        // New prints are inserted ABOVE, so whatever the reader was looking at
        // has moved down by exactly the height the new rows added. Offsetting
        // by the height change holds those same trades still under the cursor
        // instead of drifting them off-screen. Rows appended below move
        // nothing, so the offset is left alone.
        after.scrollTop = appendedBelow
            ? prevTop
            : prevTop + (after.scrollHeight - prevHeight);
    }
    if (after) _tasWatchScroll(after);
}

/* Paint further into the tape as the reader scrolls toward the end of what is
   currently rendered. Assigned rather than added, because the scroll container
   is a fresh node after every render and addEventListener would stack. */
function _tasWatchScroll(el) {
    el.onscroll = () => {
        if (_tasWindow >= _tasRows.length) return;          // all of it is out
        const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
        if (remaining > TAS_GROW_SLACK) return;
        _tasWindow = Math.min(_tasWindow + TAS_DOM_STEP, _tasRows.length);
        _tasRenderTape({ appendedBelow: true });
    };
}

function _tasRender() {
    _tasRenderTape();
    _tasRenderRail();
}

function _tasChip(state) {
    const el = document.getElementById('tasChip');
    if (!el) return;
    let text, cls;
    if (!state.market_open) {
        text = 'Market closed · completed session';
        cls = 'tas-chip-idle';
    } else if (state.streaming) {
        text = 'Live';
        cls = 'tas-chip-live';
    } else {
        text = 'Reconnecting…';
        cls = 'tas-chip-warn';
    }
    const bf = state.backfill_state || '';
    if (bf === 'running') text += ' · loading session';
    else if (bf.startsWith('unavailable')) text += ' · no history';
    el.textContent = text;
    el.className = 'tas-chip ' + cls;
    el.title = bf.startsWith('unavailable')
        ? `Backfill unavailable: ${bf.split(':').slice(1).join(':')}` : '';
}

async function tasLoad() {
    if (!_tasSymbol) return;
    if (_tasLoading) { _tasPending = true; return; }
    _tasLoading = true;
    let delay = TAS_POLL_MS;
    try {
        // Ask for the whole session. Only the first call is large — after that
        // the cursor means each poll carries just the handful of new prints.
        const url = `/api/time-and-sales?symbol=${encodeURIComponent(_tasSymbol)}`
                  + `&since=${_tasCursor}&limit=${TAS_MAX_ROWS}`
                  + (_tasEpoch != null ? `&epoch=${_tasEpoch}` : '')
                  + (_tasMinQty ? `&min_qty=${_tasMinQty}` : '');
        const res = await fetch(url);
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'request failed');

        if (data.truncated && _tasCursor > 0) {
            // We fell behind, the server restarted and its sequence reset, or
            // history caught up over the prints we hold and renumbered them.
            // Rebuild from what it just sent rather than stitching onto a
            // cursor that no longer means anything — the server answers all
            // three by sending the whole tape.
            _tasRows = [];
            _tasPainted = false;
            _tasWindow = TAS_DOM_ROWS;
        }
        if (data.epoch != null) _tasEpoch = data.epoch;
        _tasState = data;
        if (data.rows.length) {
            _tasRows.push(...data.rows);
            if (_tasRows.length > TAS_MAX_ROWS) _tasRows = _tasRows.slice(-TAS_MAX_ROWS);
        }
        // Advance past everything the server has, not merely the last row it
        // returned. With a filter most rows are skipped, and a cursor parked on
        // the last MATCH would re-scan the same span on every poll forever.
        if (data.next_seq != null) _tasCursor = data.next_seq;
        // Only touch the table when something actually changed. At ~0.2 prints
        // a second most ticks bring nothing, and repainting anyway threw away
        // the reader's scroll position, their text selection and any hover —
        // once a second, for no new information.
        if (data.rows.length || !_tasPainted) {
            _tasRenderTape();
            _tasPainted = true;
        }
        _tasRenderRail();
        _tasChip(data);

        if (!data.market_open)      delay = TAS_POLL_MS_CLOSED;
        else if (document.hidden)   delay = TAS_POLL_MS_HIDDEN;
    } catch (e) {
        console.warn('[TimeAndSales]', e);
        delay = TAS_POLL_MS_ERROR;
    } finally {
        _tasLoading = false;
    }
    if (_tasPending) { _tasPending = false; delay = 0; }
    tasScheduleLoop(delay);
}

function tasScheduleLoop(ms) {
    clearTimeout(_tasTimer);
    // A self-rescheduling timeout, never setInterval: a slow tick must not
    // stack a second request on top of the one still in flight.
    _tasTimer = setTimeout(tasLoad, ms == null ? TAS_POLL_MS : ms);
}

function tasStopLoop() {
    clearTimeout(_tasTimer);
    _tasTimer = null;
}

function tasSetSymbol(symbol) {
    if (!symbol || symbol === _tasSymbol) return;
    _tasSymbol = symbol;
    _tasRows = [];
    _tasCursor = 0;
    _tasEpoch = null;
    _tasState = {};
    _tasPainted = false;
    _tasWindow = TAS_DOM_ROWS;
    _tasRender();
    tasScheduleLoop(0);
}

async function _tasLoadContracts(root) {
    const sel = document.getElementById('tasContract');
    if (!sel) return;
    sel.innerHTML = '<option>loading…</option>';
    try {
        const res = await fetch(`/api/time-and-sales?contracts=1&root=${encodeURIComponent(root)}`);
        const data = await res.json();
        if (!data.success || !data.contracts.length) throw new Error(data.error || 'no contracts');
        sel.innerHTML = data.contracts.map(c =>
            `<option value="${c.symbol}">${DataGrid.escape(_tasContractLabel(c))}</option>`).join('');
        tasSetSymbol(data.contracts[0].symbol);
    } catch (e) {
        sel.innerHTML = '<option value="">unavailable</option>';
        console.warn('[TimeAndSales] contract list', e);
    }
}

function tasInit() {
    window._tasLoaded = true;
    const root = document.getElementById('tasRoot');
    const contract = document.getElementById('tasContract');
    const minQty = document.getElementById('tasMinQty');

    if (root)     root.addEventListener('change', () => _tasLoadContracts(root.value));
    if (contract) contract.addEventListener('change', () => tasSetSymbol(contract.value));
    if (minQty) {
        // Filtering is client-side over rows we already hold, so the funnel is
        // instant and lowering it again brings the hidden prints straight back.
        let minQtyDebounce;
        minQty.addEventListener('input', () => {
            _tasMinQty = parseInt(minQty.value, 10) || 0;
            try { localStorage.setItem('tasMinQty', String(_tasMinQty)); } catch (e) { /* private mode */ }
            // The threshold is applied server-side over the whole tape, so a
            // change means re-asking from scratch rather than re-filtering what
            // we hold. Debounced so typing "5000" is one request, not four.
            clearTimeout(minQtyDebounce);
            minQtyDebounce = setTimeout(() => {
                _tasRows = [];
                _tasCursor = 0;
                _tasPainted = false;
                _tasWindow = TAS_DOM_ROWS;
                tasScheduleLoop(0);
            }, 250);
        });
        try {
            const saved = parseInt(localStorage.getItem('tasMinQty'), 10);
            if (saved) { _tasMinQty = saved; minQty.value = saved; }
        } catch (e) { /* private mode */ }
    }

    document.addEventListener('visibilitychange', () => {
        if (!window._tasLoaded || !_tasTimer) return;
        tasScheduleLoop(document.hidden ? TAS_POLL_MS_HIDDEN : 0);
    });

    _tasLoadContracts(root ? root.value : 'NIFTY');
}
