/**
 * Order Placement (/orderplacement).
 *
 * A pad that fires one option order at every broker carrying
 * BROKER_N_OP_ACTIVE=true, and the book of what it placed sitting beside it on
 * the same screen. Nothing here reaches a broker except through send(), and
 * send() is only reachable from the second of two presses.
 *
 * The page owns no order routing of its own: /api/order-placement/* is the
 * only end it talks to, and that blueprint refuses to touch an order this page
 * did not place. Everything on the strip is therefore this page's own — an OI
 * Profile order or an algo's order is neither listed nor cancellable here.
 */

(function () {
    'use strict';

    const API = '/api/order-placement';
    const POLL_MS = 5000;

    // Statuses that are still unfilled and therefore still editable. Mirrors
    // MineOrderStore.EDITABLE_STATUSES on the backend.
    const EDITABLE = ['OPEN', 'PENDING', 'EXECUTING'];

    const $ = id => document.getElementById(id);
    const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const money = n => Number(n || 0).toLocaleString('en-IN',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const clock = ms => ms ? new Date(ms).toLocaleTimeString('en-IN',
        { hour: '2-digit', minute: '2-digit', hour12: true }) : '—';

    function toast(msg, tone) {
        if (typeof showNotification === 'function') showNotification(msg, tone);
    }

    /** "• ZERODHA 1: OK" per broker leg — what actually happened, per broker. */
    function brokerLines(r) {
        if (!Array.isArray(r?.summary) || !r.summary.length) return '';
        return '\n' + r.summary.map(s => {
            const name = String(s.broker || '').replace(/_/g, ' ').toUpperCase();
            return `• ${name}: ${s.result?.success ? 'OK' : (s.result?.error || 'Failed')}`;
        }).join('\n');
    }

    // A per-browser view preference. localStorage can throw outright (private
    // windows, blocked site data), and a page that cannot remember a
    // preference must still render — so every read and write is guarded and
    // the default is "header showing".
    const CHROME_KEY = 'op.hideChrome';

    function readChrome() {
        try { return localStorage.getItem(CHROME_KEY) === '1'; } catch (_) { return false; }
    }

    function applyChrome(hidden) {
        // The class lands on <body> because the nav bar is outside this page's
        // own markup; the stylesheet only acts on it below 600px, so a desktop
        // that once toggled it still gets its header.
        document.body.classList.toggle('op-bare', hidden);
        // Icon only: it lives in a phone topbar where every character costs
        // width, and the caret alone says which way it goes. The words are
        // still there for anything that reads the page aloud.
        const btn = $('opChrome');
        const label = hidden ? 'Show the nav bar and title'
                             : 'Hide the nav bar and title on this screen';
        btn.textContent = hidden ? '▾' : '▴';
        btn.setAttribute('aria-pressed', String(hidden));
        btn.setAttribute('aria-label', label);
        btn.title = label;
    }

    const state = {
        ltp: null,          // {key, price, at} — the premium last seen
        brokers: [],
        symbol: '',
        step: 0,            // 0 = not known yet; never a guessed default
        stepSource: '',
        lotSize: 0,
        spot: null,
        expiry: null,
        optionType: 'CE',
        action: 'BUY',
        // STOP, matching the pad's markup. Keep the two in step: this is what
        // the first order of a session goes out as if nothing is touched.
        orderType: 'SL-M',
        armed: false,       // the review bar is showing the order about to go
        bookSig: null,
    };

    // ── config: who this page can reach ──────────────────────────────

    async function loadConfig() {
        let data;
        try {
            const res = await fetch(`${API}/config`);
            data = await res.json();
        } catch (e) {
            data = { success: false, error: e.message };
        }
        if (!data.success) {
            $('opBrokers').classList.add('op-brokers--warn');
            $('opBrokers').innerHTML =
                `<span class="op-chip op-chip-off">${esc(data.error || 'Config unavailable')}</span>`;
            return;
        }

        state.brokers = data.brokers || [];
        const host = $('opBrokers');
        // The chips are hidden on a phone to buy the ticket its width back —
        // except when they carry the warning, which is the one thing on this
        // bar that explains why the button below is dead. The stylesheet keys
        // off this class rather than the chip inside, so the rule works
        // without :has().
        host.classList.toggle('op-brokers--warn', !state.brokers.length);
        if (!state.brokers.length) {
            // Not an error state to be silent about: with no broker opted in
            // the button below can only ever fail, so say why here instead.
            host.innerHTML = '<span class="op-chip op-chip-off">No broker enabled — set ' +
                '<code>BROKER_N_OP_ACTIVE=true</code></span>';
        } else {
            host.innerHTML = state.brokers.map(b =>
                `<span class="op-chip" title="Broker ${esc(b.instance)} · ${esc(b.type)}">` +
                `${esc(b.name)}${b.lots ? ` <em>×${esc(b.lots)}</em>` : ''}</span>`).join('');
        }

        const sel = $('opSymbol');
        if (!sel.options.length) {
            sel.innerHTML = (data.symbols || []).map(s =>
                `<option value="${esc(s)}">${esc(s)}</option>`).join('');
            state.symbol = sel.value;
        }
        syncPlaceButton();
    }

    function syncPlaceButton() {
        const btn = $('opPlace');
        btn.disabled = !state.brokers.length;
        btn.title = state.brokers.length
            ? `Goes to ${state.brokers.length} broker${state.brokers.length === 1 ? '' : 's'}`
            : 'No broker is enabled for this page';
    }

    // ── contract: spot, strike step, lot size, premium ───────────────

    /**
     * The step, lot size and spot for the underlying on screen — one call,
     * answered from that underlying's own option chain.
     *
     * The step is cleared before the call rather than defaulted: carrying the
     * previous underlying's difference over would put the ± buttons on strikes
     * this chain does not list (NIFTY steps 50, BANKNIFTY and SENSEX 100), and
     * a wrong strike is a rejected order at best.
     */
    async function loadContract() {
        const symbol = state.symbol;
        if (!symbol) return;          // config has not answered yet

        state.step = 0;
        state.stepSource = '';
        state.lotSize = 0;
        state.spot = null;
        state.expiry = null;
        syncStrikeControls();

        try {
            const res = await fetch(`${API}/contract?symbol=${encodeURIComponent(symbol)}`);
            const d = await res.json();
            if (symbol !== state.symbol) return;      // the user moved on
            if (!d.success) throw new Error(d.error || 'Contract unavailable');
            state.step = Number(d.strike_step) || 0;
            state.stepSource = d.step_source || '';
            state.lotSize = Number(d.lot_size) || 0;
            state.spot = Number(d.spot) || null;
            state.expiry = d.expiry || null;
        } catch (e) {
            toast(`${symbol}: ${e.message}`, 'error');
        }

        renderSpot();
        syncStrikeControls();
        if (!$('opStrike').value && state.spot && state.step) $('opStrike').value = atmStrike();
        renderContract();
    }

    function atmStrike() {
        if (!state.spot || !state.step) return '';
        return Math.round(state.spot / state.step) * state.step;
    }

    /** No step, no stepping: the ± and ATM buttons can only guess without one. */
    function syncStrikeControls() {
        const known = !!state.step;
        for (const id of ['opStrikeUp', 'opStrikeDown', 'opAtm']) {
            $(id).disabled = !known;
        }
        $('opStrikeUp').title = known ? `One strike up (+${state.step})` : 'Strike step unknown';
        $('opStrikeDown').title = known ? `One strike down (−${state.step})` : 'Strike step unknown';
        $('opAtm').disabled = !known || !state.spot;
    }

    function renderSpot() {
        const bits = [];
        if (state.spot) bits.push(`₹${money(state.spot)}`);
        if (state.step) {
            // Says when the step is the built-in fallback rather than this
            // chain's own difference, so a surprising number is traceable.
            bits.push(`step ${state.step}${state.stepSource === 'fallback' ? '*' : ''}`);
        }
        if (state.lotSize) bits.push(`lot ${state.lotSize}`);
        if (state.expiry) bits.push(state.expiry);
        // Says the spot is unavailable rather than printing a dash that could
        // be read as zero — the ATM button depends on it.
        $('opSpot').textContent = bits.length ? bits.join(' · ') : 'contract unavailable';
        $('opSpot').title = state.stepSource === 'fallback'
            ? 'Strike step from the built-in table — the live chain could not be read'
            : (state.stepSource === 'chain' ? "Strike step read from this chain's own strikes" : '');
    }

    /**
     * The one price box, dressed for whichever order type is selected.
     *
     * A LIMIT rests AT its number; a STOP triggers ON its number and then goes
     * to market. Those are different promises, so the label and the hint change
     * with the type rather than leaving one "price" box to mean two things.
     */
    function renderPriceField() {
        const stop = state.orderType === 'SL-M';
        $('opPriceField').hidden = state.orderType === 'MARKET';
        $('opPriceLabel').textContent = stop ? 'Trigger price' : 'Limit price';
        $('opStopHint').hidden = !stop;
        $('opStopHint').textContent = stop
            ? (state.action === 'SELL'
                ? 'Stop-loss — sells at market when the premium falls to the trigger.'
                : 'Stop entry — buys at market when the premium rises to the trigger.')
            : '';
    }

    function renderContract() {
        const strike = $('opStrike').value;
        $('opContract').textContent = strike
            ? `${state.symbol} ${strike} ${state.optionType}`
            : '—';
    }

    async function fillLtp() {
        const strike = Number($('opStrike').value);
        if (!strike) { toast('Enter a strike first', 'error'); return; }
        const btn = $('opLtp');
        btn.disabled = true;
        try {
            const qs = new URLSearchParams({
                symbol: state.symbol, strike: String(strike), option_type: state.optionType,
            });
            const res = await fetch(`/api/option-ltp?${qs}`);
            const d = await res.json();
            if (!d.success) throw new Error(d.error || 'No quote');
            $('opLimitPrice').value = Number(d.ltp).toFixed(2);
            rememberLtp(Number(d.ltp));
            // Says where the number came from, so a stale prefill reads as one
            // rather than as a live quote.
            $('opLtpHint').textContent = `${d.opt_symbol || ''} last traded ₹${money(d.ltp)} `
                + `at ${new Date().toLocaleTimeString('en-IN', { hour12: true })}`;
        } catch (e) {
            $('opLtpHint').textContent = '';
            toast(`LTP failed: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
        }
    }

    // ── the stop-direction guard ─────────────────────────────────────

    /** Key for "the premium we last saw", so a stale one is never reused. */
    function contractKey() {
        return `${state.symbol}|${$('opStrike').value}|${state.optionType}`;
    }

    function rememberLtp(ltp) {
        state.ltp = { key: contractKey(), price: ltp, at: Date.now() };
    }

    /** The last premium for the contract on screen, or null. */
    function knownLtp() {
        const seen = state.ltp;
        if (!seen || seen.key !== contractKey()) return null;
        // A quote from several minutes ago is not the market any more, and a
        // stale one would either block a good stop or pass a bad one.
        if (Date.now() - seen.at > 60000) return null;
        return seen.price;
    }

    /**
     * Refuse a stop that is already triggered — the same rule and the same
     * words as the OI Profile panel.
     *
     * A stop rests until the premium TOUCHES its trigger and then goes to
     * market, so a BUY stop only waits if it sits ABOVE the market and a SELL
     * stop only if it sits BELOW. The wrong way round it fires the instant it
     * reaches the exchange, at market, for the full size. The broker will not
     * refuse it — a triggered stop is a legitimate order — so this and its
     * twin on the server are the only places it can be caught.
     *
     * No quote, no check: the feed can be empty, and refusing to place because
     * we cannot see a price would be worse than placing.
     */
    function stopDirectionError(action, trigger, last) {
        if (!(last > 0)) return '';
        if (action === 'BUY' && trigger <= last) {
            return `A BUY stop must sit ABOVE the market — ₹${money(trigger)} is at or below `
                 + `the last price of ₹${money(last)}, so it would trigger instantly. `
                 + `Use MARKET to buy now, or raise the trigger.`;
        }
        if (action === 'SELL' && trigger >= last) {
            return `A SELL stop must sit BELOW the market — ₹${money(trigger)} is at or above `
                 + `the last price of ₹${money(last)}, so it would trigger instantly. `
                 + `Use MARKET to sell now, or lower the trigger.`;
        }
        return '';
    }

    /** The premium for the contract on screen, fetched if we have none fresh. */
    async function currentLtp() {
        const known = knownLtp();
        if (known !== null) return known;
        try {
            const qs = new URLSearchParams({
                symbol: state.symbol, strike: $('opStrike').value,
                option_type: state.optionType,
            });
            const res = await fetch(`/api/option-ltp?${qs}`);
            const d = await res.json();
            if (!d.success || !(Number(d.ltp) > 0)) return null;
            rememberLtp(Number(d.ltp));
            return Number(d.ltp);
        } catch (_) {
            return null;      // unknown, which means unchecked
        }
    }

    // ── placing ──────────────────────────────────────────────────────

    /**
      * The order, as the page has it. Size is not in here and never was a
      * field: each broker trades its own BROKER_N_OP_LOTS, which is the only
      * sizing that can differ per account the way the accounts do.
      */
    function readForm() {
        const order = {
            symbol: state.symbol,
            strike: parseInt($('opStrike').value, 10),
            option_type: state.optionType,
            action: state.action,
            order_type: state.orderType,
        };
        const typed = Number($('opLimitPrice').value);
        if (state.orderType === 'LIMIT') order.limit_price = typed;
        if (state.orderType === 'SL-M') order.trigger_price = typed;
        return order;
    }

    /**
     * "Kavin (Kite) ×20, Fyers (default)" — what each broker will trade.
     *
     * A broker with no BROKER_N_OP_LOTS is named as taking the default rather
     * than given a number: the dispatcher falls back to BROKER_N_LOT_SIZE and
     * then to one lot, and printing a guess at which would be worse than
     * saying it is not set here.
     */
    function brokerSizes() {
        return state.brokers
            .map(b => b.lots ? `${b.name} ×${b.lots}` : `${b.name} (default)`)
            .join(', ');
    }

    /** The one place a typed field is judged — the server re-checks all of it. */
    function problemWith(order) {
        if (!state.brokers.length) return 'No broker is enabled for this page';
        if (!Number.isInteger(order.strike) || order.strike <= 0) return 'Enter a strike above zero';
        if (order.order_type === 'LIMIT' && !(Number(order.limit_price) > 0))
            return 'A LIMIT order needs a price above zero';
        if (order.order_type === 'SL-M' && !(Number(order.trigger_price) > 0))
            return 'A STOP order needs a trigger price above zero';
        return '';
    }

    function setMsg(text, tone) {
        const el = $('opMsg');
        el.textContent = text || '';
        el.className = 'op-msg' + (tone ? ` op-msg-${tone}` : '');
    }

    /** First press: show exactly what would go out, and to whom. */
    async function review() {
        const order = readForm();
        const problem = problemWith(order);
        if (problem) { setMsg(problem, 'err'); return; }

        // A stop is checked against the market before it is even shown for
        // confirmation: the review bar should never offer to place an order
        // that would fire the moment it arrives.
        if (order.order_type === 'SL-M') {
            setMsg('Checking the trigger against the market…');
            const wrongSide = stopDirectionError(order.action, order.trigger_price,
                                                 await currentLtp());
            if (wrongSide) { setMsg(wrongSide, 'err'); return; }
        }

        // A stop is named for what it does, not for its order type: "SL-M"
        // on the last screen before a live order says less than "stop-loss".
        let price = 'MARKET';
        if (order.order_type === 'LIMIT') {
            price = `LIMIT ₹${money(order.limit_price)}`;
        } else if (order.order_type === 'SL-M') {
            price = `${order.action === 'SELL' ? 'STOP-LOSS' : 'STOP ENTRY'} `
                  + `— triggers at ₹${money(order.trigger_price)}`;
        }

        // Every broker's own size is spelled out here rather than summarised:
        // this bar is the last thing read before the order goes, and "how
        // many" is the one number the pad itself does not show.
        $('opConfirmText').innerHTML =
            `<b class="op-${order.action.toLowerCase()}">${esc(order.action)}</b> ` +
            `${esc(order.symbol)} ${esc(order.strike)} ${esc(order.option_type)} · ` +
            `${esc(price)}<br>` +
            `<span class="op-confirm-where">→ ${esc(state.brokers.length)} broker` +
            `${state.brokers.length === 1 ? '' : 's'} · lots: ${esc(brokerSizes())}</span>`;
        $('opConfirm').hidden = false;
        state.armed = true;
        setMsg('');
        $('opConfirmSend').focus();
    }

    function disarm() {
        state.armed = false;
        $('opConfirm').hidden = true;
        $('opConfirmSend').disabled = false;
        $('opConfirmSend').textContent = 'Place order';
    }

    async function send() {
        const order = readForm();
        const problem = problemWith(order);
        if (problem) { disarm(); setMsg(problem, 'err'); return; }

        const btn = $('opConfirmSend');
        // Guarded rather than merely styled: a double-click here is two
        // positions, and the button stays dead until the reply lands.
        btn.disabled = true;
        btn.textContent = 'Placing…';
        try {
            const res = await fetch(`${API}/order`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                body: JSON.stringify(order),
            });
            const r = await res.json();
            if (!r.success) throw new Error(r.error || 'Order rejected');
            disarm();
            const where = `${r.brokers_targeted} broker${r.brokers_targeted === 1 ? '' : 's'}`;
            setMsg(order.order_type === 'SL-M'
                ? `Stop ${order.action} ${order.symbol} ${order.strike} ${order.option_type} `
                  + `resting at ₹${money(order.trigger_price)} — triggers when the premium `
                  + `touches it (${where})`
                : `${order.action} ${order.symbol} ${order.strike} ${order.option_type} `
                  + `— ${r.status === 'OPEN' ? 'resting' : 'placed'} at ${where}`, 'ok');
            toast(`Order ${r.status === 'OPEN' ? 'resting' : 'placed'}${brokerLines(r)}`, 'success');
            state.bookSig = null;
            loadBook();
        } catch (e) {
            // Left armed on failure: the reason matters more than the bar, and
            // re-reviewing means retyping nothing.
            btn.disabled = false;
            btn.textContent = 'Retry';
            setMsg(e.message, 'err');
        }
    }

    // ── the book: what this page placed, still on this page ──────────

    async function fetchBook() {
        try {
            const res = await fetch(`${API}/orders?sync=1`);
            if (!res.ok) return null;
            return await res.json();
        } catch (_) { return null; }
    }

    function pendingRow(o) {
        const stop = String(o.order_type || o.type || '').toUpperCase().startsWith('SL');
        const legs = (o.broker_order_ids || [])
            .filter(b => b?.order_id || b?.result?.order_id).length;
        // A stop is waiting on a trigger, so it reads as neither a live BUY nor
        // a live SELL. SL is an exit stop, ST a stop entry — calling a resting
        // BUY a "stop-loss" would read as protection on a position that does
        // not exist yet.
        const side = stop ? 'stop' : (o.action === 'BUY' ? 'buy' : 'sell');
        const badge = stop ? (o.action === 'BUY' ? 'ST' : 'SL')
                           : (o.action === 'BUY' ? 'B' : 'S');
        return `
            <div class="op-po ${side}" data-id="${esc(o.id)}">
                <span class="op-po-side" title="${esc(stop
                    ? (o.action === 'BUY' ? 'Stop entry — buys in at the trigger'
                                          : 'Stop-loss — sells out at the trigger')
                    : `${o.action} — live at ${legs} broker${legs === 1 ? '' : 's'}`)}"
                    >${esc(badge)}</span>
                <span class="op-po-inst">
                    <b>${esc(o.symbol)} ${esc(o.strike)}${esc(o.option_type)}</b>
                    <small>${esc(o.order_type || o.type || '')} · qty ${esc(o.quantity || 0)}
                        · ${esc(legs)} broker${legs === 1 ? '' : 's'} · ${esc(clock(o.created_at))}</small>
                </span>
                <span class="op-po-edit">
                    <input type="number" class="op-po-price" step="0.05" min="0"
                           value="${esc(o.price || 0)}" inputmode="decimal"
                           title="New ${stop ? 'trigger' : 'limit'} price — applied at every broker">
                    <button class="op-po-btn save" type="button"
                            title="Update the ${stop ? 'trigger' : 'price'} at every broker">&#10003;</button>
                    <button class="op-po-btn cancel" type="button"
                            title="Cancel this ${stop ? 'stop' : 'order'} at every broker">&times;</button>
                </span>
            </div>`;
    }

    function doneRow(o) {
        const cls = o.status === 'EXECUTED' ? 'ok' : (o.status === 'CANCELLED' ? 'mute' : 'err');
        const kind = String(o.order_type || o.type || '');
        const at = o.status === 'EXECUTED' && o.entry_price
            ? ` @ ₹${money(o.entry_price)}` : '';
        return `
            <div class="op-done-row">
                <span class="op-done-inst">${esc(o.action)} ${esc(o.symbol)} ` +
                    `${esc(o.strike)}${esc(o.option_type)}</span>
                <span class="op-done-meta">${esc(kind.toUpperCase().startsWith('SL')
                        ? (o.action === 'BUY' ? 'STOP ENTRY' : 'STOP-LOSS') : kind)} ·
                    qty ${esc(o.quantity || 0)}${esc(at)}</span>
                <span class="op-done-status op-${cls}">${esc(o.status || '')}</span>
                <span class="op-done-time">${esc(clock(o.created_at))}</span>
            </div>`;
    }

    async function loadBook() {
        const data = await fetchBook();
        if (!data || !data.success) { $('opDot').className = 'op-dot op-dot-err'; return; }
        $('opDot').className = 'op-dot op-dot-ok';

        const pending = (data.pending || []).filter(o => EDITABLE.includes(o.status));
        const done = data.done || [];

        // Re-rendering under a half-typed price would throw the edit away, and
        // an unchanged book has nothing to redraw anyway.
        const sig = JSON.stringify([pending.map(o => [o.id, o.status, o.price, o.quantity]),
                                    done.map(o => [o.id, o.status])]);
        const typing = document.activeElement?.classList?.contains('op-po-price');
        if (sig === state.bookSig || typing) return;
        state.bookSig = sig;

        $('opPendingCount').textContent = pending.length;
        $('opCancelAll').hidden = !pending.length;
        $('opPending').innerHTML = pending.length
            ? pending.map(pendingRow).join('')
            : '<p class="op-empty">Nothing resting. A LIMIT order placed here stays on '
              + 'this strip until it fills or is cancelled.</p>';

        $('opDoneCount').textContent = done.length;
        $('opDone').innerHTML = done.length
            ? done.map(doneRow).join('')
            : '<p class="op-empty">No orders from this page today.</p>';
    }

    async function submitRow(row, isCancel) {
        const id = row.dataset.id;
        const price = parseFloat(row.querySelector('.op-po-price')?.value);
        if (!isCancel && (isNaN(price) || price <= 0)) {
            toast('Enter a valid price', 'error');
            return;
        }
        row.querySelectorAll('.op-po-btn').forEach(b => { b.disabled = true; });
        try {
            const res = await fetch(isCancel ? `${API}/orders/${id}` : `${API}/orders/${id}/price`, {
                method: isCancel ? 'DELETE' : 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                body: isCancel ? undefined : JSON.stringify({ price }),
            });
            const r = await res.json();
            if (r.success) {
                const stop = row.classList.contains('stop');
                toast(isCancel ? `${stop ? 'Stop' : 'Order'} cancelled${brokerLines(r)}`
                               : `${stop ? 'Trigger' : 'Price'} → ₹${money(price)}${brokerLines(r)}`,
                      'success');
            } else if (r.gone) {
                // It finished at the broker while it was still on the strip.
                // The server has already corrected the record, so the redraw
                // below drops the row — a warning, not something to retry.
                row.classList.add('op-po--gone');
                toast(r.error || 'Order is no longer open', 'warning');
            } else {
                toast(`${isCancel ? 'Cancel' : 'Update'} failed: ${r.error || 'Unknown error'}`
                      + brokerLines(r), 'error');
            }
        } catch (e) {
            toast(`${isCancel ? 'Cancel' : 'Update'} error: ${e.message}`, 'error');
        } finally {
            state.bookSig = null;   // force the next render to redraw
            loadBook();
        }
    }

    /**
     * Cancel every resting order, one request each, sequentially — each cancel
     * already fans out to every broker the order went to, and firing all of
     * them at once is the surest way to trip a broker's rate limit and have
     * half fail for a reason that has nothing to do with the orders.
     */
    async function cancelAll(btn) {
        const ids = [...document.querySelectorAll('#opPending .op-po')].map(r => r.dataset.id);
        if (!ids.length) return;
        if (!confirm(`Cancel all ${ids.length} resting order${ids.length === 1 ? '' : 's'} `
                     + 'at every broker? This cannot be undone.')) return;

        btn.disabled = true;
        btn.textContent = 'Cancelling…';
        let done = 0, gone = 0;
        const failed = [];
        for (const id of ids) {
            try {
                const res = await fetch(`${API}/orders/${id}`, {
                    method: 'DELETE', headers: { 'X-CSRFToken': csrf() },
                });
                const r = await res.json();
                if (r.success) done++;
                else if (r.gone) gone++;
                else failed.push(r.error || 'Unknown error');
            } catch (e) {
                failed.push(e.message);
            }
        }
        const parts = [];
        if (done) parts.push(`${done} cancelled`);
        if (gone) parts.push(`${gone} already done at the broker`);
        if (failed.length) parts.push(`${failed.length} failed`);
        toast(parts.join(', ') + (failed.length ? `\n• ${failed[0]}` : ''),
              failed.length ? 'error' : 'success');

        btn.disabled = false;
        btn.textContent = 'Cancel all';
        state.bookSig = null;
        loadBook();
    }

    // ── wiring ───────────────────────────────────────────────────────

    function segment(hostId, onPick) {
        $(hostId).addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-value]');
            if (!btn) return;
            [...e.currentTarget.querySelectorAll('button')].forEach(b =>
                b.classList.toggle('active', b === btn));
            onPick(btn.dataset.value);
        });
    }

    function init() {
        if (!$('opPlace')) return;

        segment('opOptionType', v => {
            state.optionType = v;
            state.ltp = null;
            $('opLtpHint').textContent = '';
            renderContract();
            disarm();
        });
        segment('opAction', v => {
            state.action = v;
            // A stop reads as the opposite thing on the opposite side, so the
            // hint under the trigger has to follow the side.
            renderPriceField();
            disarm();
        });
        segment('opOrderType', v => {
            state.orderType = v;
            renderPriceField();
            disarm();
        });

        $('opSymbol').addEventListener('change', () => {
            state.symbol = $('opSymbol').value;
            $('opStrike').value = '';
            $('opLtpHint').textContent = '';
            state.ltp = null;
            disarm();
            loadContract();
        });

        $('opStrike').addEventListener('input', () => { renderContract(); disarm(); });
        $('opStrikeUp').addEventListener('click', () => stepStrike(+1));
        $('opStrikeDown').addEventListener('click', () => stepStrike(-1));
        $('opAtm').addEventListener('click', () => {
            const atm = atmStrike();
            if (!atm) { toast('Spot price unavailable — type the strike', 'error'); return; }
            $('opStrike').value = atm;
            renderContract();
            disarm();
        });
        $('opLtp').addEventListener('click', fillLtp);
        $('opLimitPrice').addEventListener('input', disarm);

        $('opPlace').addEventListener('click', review);
        $('opConfirmCancel').addEventListener('click', () => { disarm(); setMsg(''); });
        $('opConfirmSend').addEventListener('click', send);

        // Enter never places an order: it only ever gets as far as the review
        // bar, which is the same first press the button gives.
        $('opPlace').closest('.op-pad').addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            if (e.target.closest('.op-confirm')) return;
            e.preventDefault();
            review();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (state.armed) { disarm(); setMsg(''); }
            $('opInfo').parentElement.classList.remove('is-open');
            $('opInfo').setAttribute('aria-expanded', 'false');
        });

        $('opPending').addEventListener('click', (e) => {
            const row = e.target.closest('.op-po');
            if (!row) return;
            if (e.target.closest('.op-po-btn.cancel')) submitRow(row, true);
            else if (e.target.closest('.op-po-btn.save')) submitRow(row, false);
        });
        $('opPending').addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' || !e.target.classList.contains('op-po-price')) return;
            e.preventDefault();
            e.target.blur();
            submitRow(e.target.closest('.op-po'), false);
        });
        $('opCancelAll').addEventListener('click', (e) => cancelAll(e.currentTarget));

        // The ⓘ opens on hover from CSS alone; this is the tap path, and the
        // ways back out of it — the same button, Escape, or a press anywhere
        // else. Without it the panel would be unreachable on a phone.
        const info = $('opInfo');
        const infoWrap = info.parentElement;
        const setInfo = (open) => {
            infoWrap.classList.toggle('is-open', open);
            info.setAttribute('aria-expanded', String(open));
        };
        info.addEventListener('click', (e) => {
            e.stopPropagation();
            setInfo(!infoWrap.classList.contains('is-open'));
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.op-info-wrap')) setInfo(false);
        });

        applyChrome(readChrome());
        $('opChrome').addEventListener('click', () => {
            const hidden = !document.body.classList.contains('op-bare');
            applyChrome(hidden);
            try { localStorage.setItem(CHROME_KEY, hidden ? '1' : '0'); } catch (_) { /* not fatal */ }
        });

        $('opReload').addEventListener('click', () => {
            state.bookSig = null;
            loadConfig();
            loadContract();
            loadBook();
        });

        renderPriceField();
        loadConfig().then(loadContract);
        loadBook();
        setInterval(() => { if (!document.hidden) loadBook(); }, POLL_MS);
    }

    function stepStrike(dir) {
        const step = state.step;
        if (!step) return;      // the buttons are disabled, but never guess
        const current = Number($('opStrike').value) || atmStrike() || 0;
        const next = Math.max(step, Math.round(current / step) * step + dir * step);
        $('opStrike').value = next;
        renderContract();
        disarm();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
