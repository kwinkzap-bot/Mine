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
        mode: 'signal',     // 'signal' arms a ladder; 'single' places one order
        plan: null,         // the last server reading of the pasted tip
        signalSig: null,
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
        $('opConfirmSend').textContent =
            state.mode === 'signal' ? 'Arm signal' : 'Place order';
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

    // ── signal mode ──────────────────────────────────────────────────
    // A tip pasted here becomes one entry stop and a plan. Nothing on this
    // side parses the text or decides whether it is armable: both are asked of
    // /signal/parse, because this page is one caller of the arm route and a
    // parser bug here would be a live order at the wrong strike.

    /** The active button's value in one of the pad's segments. */
    function segValue(hostId) {
        return $(hostId).querySelector('button.active')?.dataset.value || '';
    }

    /** Set a segment from the parsed tip, through its own click handler so the
     *  state it keeps and the redraws it triggers all happen as usual. */
    function setSeg(hostId, value) {
        const btn = [...$(hostId).querySelectorAll('button')]
            .find(b => b.dataset.value === value);
        if (btn && !btn.classList.contains('active')) btn.click();
    }

    const SIG_FIELDS = { entry: 'opSigEntry', stop: 'opSigStop' };
    const SIG_TARGETS = ['opSigT1', 'opSigT2', 'opSigT3'];

    function setMode(mode) {
        state.mode = mode === 'signal' ? 'signal' : 'single';
        const signal = state.mode === 'signal';
        document.querySelectorAll('.op-signal-only')
            .forEach(el => { el.hidden = !signal; });
        document.querySelectorAll('.op-single-only')
            .forEach(el => { el.hidden = signal; });
        if (!signal) renderPriceField();          // it owns its own hidden state
        $('opMode').querySelectorAll('button').forEach(b =>
            b.classList.toggle('active', b.dataset.value === state.mode));
        $('opPlace').textContent = signal ? 'Review signal' : 'Review order';
        document.body.classList.toggle('op-signal-mode', signal);
        // Never carry a half-typed ticket across a mode switch: what the
        // review bar is showing is not what the other mode would send.
        disarm();
        setMsg('');
        if (signal) readSignal();
    }

    /** The ladder as it stands on screen. */
    function signalForm() {
        const num = id => {
            const v = parseFloat($(id).value);
            return Number.isFinite(v) && v > 0 ? v : null;
        };
        const targets = SIG_TARGETS.map(num).filter(v => v !== null);
        return {
            symbol: $('opSymbol').value,
            strike: parseInt($('opStrike').value, 10) || null,
            option_type: segValue('opOptionType'),
            action: segValue('opAction'),
            entry: num(SIG_FIELDS.entry),
            stop: num(SIG_FIELDS.stop),
            targets: targets.length ? targets : null,
        };
    }

    /** Ask the server what the ticket would do, and why it would refuse it.
     *
     *  Every check lives on the server, not here: this page is one caller of
     *  the arm route, and a rule enforced only in this file is a rule a bad
     *  request walks straight past.
     */
    async function readSignal() {
        if (state.mode !== 'signal') return;
        const payload = signalForm();
        if (!payload.entry) {
            $('opSignalRead').textContent = '';
            $('opSignalPlan').textContent = '';
            state.plan = null;
            return;
        }

        try {
            const res = await fetch(`${API}/signal/parse`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                body: JSON.stringify(payload),
            });
            const r = await res.json();
            if (!r.success) {
                state.plan = null;
                $('opSignalRead').textContent = r.error || 'Could not read that';
                $('opSignalRead').className = 'op-hint op-hint-err op-signal-only';
                $('opSignalPlan').textContent = '';
                return;
            }
            state.plan = r;

            const p = r.plan || {};
            $('opSignalRead').textContent =
                `${p.action} ${p.symbol} ${p.strike} ${p.option_type}`
                + (p.expiry ? ` · ${p.expiry}` : '')
                + (r.ltp ? ` · premium ₹${money(r.ltp)}` : '');
            $('opSignalRead').className = 'op-hint op-signal-only';
            renderSignalPlan(r);
        } catch (e) {
            $('opSignalRead').textContent = e.message;
            $('opSignalRead').className = 'op-hint op-hint-err op-signal-only';
        }
    }

    /** What arming would do, per broker — and why it would not. */
    function renderSignalPlan(r) {
        const el = $('opSignalPlan');
        if (r.error) {
            el.innerHTML = `<span class="op-hint-err">${esc(r.error)}</span>`;
            return;
        }
        const ready = (r.brokers || []).filter(b => b.signal_ready);
        if (!ready.length) {
            el.innerHTML = '<span class="op-hint-err">No broker is sized for signal mode — '
                + 'set <code>BROKER_N_OP_SIGNAL_LOTS</code></span>';
            return;
        }
        const where = ready.map(b =>
            `${esc(b.name)} <em>${esc(b.signal_entry_lots)} lots</em>`).join(' · ');
        const per = ready.length === 1 ? `${esc(ready[0].signal_lots)} lot` : 'one leg';
        // Spelled out because the sizing is the part that surprises: the stop
        // is a leg like the targets, not cover for the whole position.
        el.innerHTML =
            `Entry stop now at ${where}. On fill it becomes three equal orders of `
            + `${per} each — stop, target 1, target 2 — so nothing more than the `
            + `position is ever offered for sale. Target 3 `
            + `(₹${money(r.target_3_watched)}) rides with the stop and is watched `
            + `by the app.`;
    }

    async function reviewSignal() {
        await readSignal();
        const r = state.plan;
        if (!r) { setMsg('Paste a signal first', 'err'); return; }
        if (r.error) { setMsg(r.error, 'err'); return; }

        const p = r.plan || {};
        const ready = (r.brokers || []).filter(b => b.signal_ready);
        const t = p.targets || [];
        $('opConfirmText').innerHTML =
            `<b class="op-${String(p.action).toLowerCase()}">${esc(p.action)}</b> `
            + `${esc(p.symbol)} ${esc(p.strike)} ${esc(p.option_type)} · `
            + `STOP ENTRY — triggers at ₹${money(p.entry)}<br>`
            + `<span class="op-confirm-where">Going out now: the entry only, at `
            + `${ready.map(b => `${esc(b.name)} ×${esc(b.signal_entry_lots)}`).join(', ')}.</span>`
            + `<span class="op-confirm-where">On fill, three equal orders: stop `
            + `₹${money(p.stop)}, T1 ₹${money(t[0])}, T2 ₹${money(t[1])}. `
            + `T3 ₹${money(t[2])} rides with the stop, watched by the app. A stop `
            + `hit cancels the targets and exits the rest at market.</span>`;
        $('opConfirm').hidden = false;
        $('opConfirmSend').textContent = 'Arm signal';
        state.armed = true;
        setMsg('');
        $('opConfirmSend').focus();
    }

    async function sendSignal() {
        const btn = $('opConfirmSend');
        btn.disabled = true;
        btn.textContent = 'Arming…';
        try {
            const res = await fetch(`${API}/signal`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                body: JSON.stringify(signalForm()),
            });
            const r = await res.json();
            if (!r.success) throw new Error(r.error || 'Signal refused');
            disarm();
            const ok = (r.summary || []).filter(b => b.success);
            setMsg(`Signal armed — entry stop resting at ${ok.length} broker`
                   + `${ok.length === 1 ? '' : 's'}. The stop and targets go on when it fills.`,
                   'ok');
            toast('Signal armed', 'success');
            state.signalSig = state.bookSig = null;
            loadSignals();
            loadBook();
        } catch (e) {
            btn.disabled = false;
            btn.textContent = 'Retry';
            setMsg(e.message, 'err');
        }
    }

    // ── live signals ─────────────────────────────────────────────────

    const STAGE_TEXT = {
        PENDING_ENTRY: 'waiting for the trigger',
        LIVE: 'in — stop and targets working',
        T1_DONE: 'target 1 booked · stop at entry',
        T2_DONE: 'target 2 booked · stop at target 1',
        FLAT: 'out',
        NO_FILL: 'never triggered',
        DEAD: 'refused',
    };

    function signalCard(s) {
        const slots = Object.values(s.brokers || {});
        const live = slots.filter(b => !['FLAT', 'NO_FILL', 'DEAD'].includes(b.stage));
        const t = s.targets || [];
        const rows = slots.map(b => {
            const stage = STAGE_TEXT[b.stage] || String(b.stage || '').toLowerCase();
            const fill = b.entry_fill ? ` · in at ₹${money(b.entry_fill)}` : '';
            const held = b.open_qty ? ` · ${esc(b.open_qty)} held` : '';
            const stop = b.stop_level ? ` · stop ₹${money(b.stop_level)}` : '';
            return `<div class="op-sig-broker op-sig-${esc(String(b.stage || '').toLowerCase())}">`
                 + `<span class="op-sig-bname">${esc(b.name || `Broker ${b.instance}`)}</span>`
                 + `<span class="op-sig-stage">${esc(stage)}${fill}${held}${stop}</span></div>`;
        }).join('');

        const done = ['DONE', 'CANCELLED', 'FAILED'].includes(s.phase);
        return `<article class="op-sig${done ? ' op-sig--done' : ''}" data-id="${esc(s.id)}">`
            + `<header class="op-sig-hd">`
            + `<span class="op-sig-contract">${esc(s.action)} ${esc(s.symbol)} `
            + `${esc(s.strike)}${esc(s.option_type)}</span>`
            + `<span class="op-sig-ladder">₹${money(s.entry)} · SL ₹${money(s.stop)} · `
            + `${t.map(v => `₹${money(v)}`).join(' → ')}</span>`
            + (done
                ? `<span class="op-sig-phase">${esc(String(s.phase).toLowerCase())}</span>`
                : `<button class="op-btn op-btn-danger op-btn-sm op-sig-x" type="button"
                           title="Cancel this signal's resting legs and square off what it holds"
                           >Stand down</button>`)
            + `</header>${rows}`
            + (live.length ? '' : '<p class="op-sig-note">Nothing left working.</p>')
            + `</article>`;
    }

    async function loadSignals() {
        let data;
        try {
            const res = await fetch(`${API}/signals`);
            if (!res.ok) return;
            data = await res.json();
        } catch (_) { return; }
        if (!data.success) return;

        const signals = data.signals || [];
        // Same redraw guard as the book: a card rebuilt under a press eats it.
        const sig = JSON.stringify([data.engine_running, signals.map(s =>
            [s.id, s.phase, Object.values(s.brokers || {}).map(b => [b.stage, b.open_qty,
                                                                    b.stop_level])])]);
        if (sig === state.signalSig) return;
        state.signalSig = sig;

        $('opSignalsWrap').hidden = !signals.length;
        $('opSignalCount').textContent = signals.length;
        const running = data.engine_running;
        $('opEngine').textContent = running ? 'watching' : '';
        $('opEngine').className = 'op-engine' + (running ? ' is-on' : '');
        $('opSignals').innerHTML = signals.map(signalCard).join('');
    }

    async function standDown(btn) {
        const card = btn.closest('.op-sig');
        // Two presses, like every other way out of a position on this page.
        if (btn.dataset.armed !== '1') {
            btn.dataset.armed = '1';
            btn.textContent = 'Confirm?';
            btn.classList.add('op-armed');
            setTimeout(() => {
                if (!btn.isConnected || btn.dataset.armed !== '1') return;
                btn.dataset.armed = '0';
                btn.textContent = 'Stand down';
                btn.classList.remove('op-armed');
            }, 3000);
            return;
        }
        btn.disabled = true;
        btn.textContent = 'Standing down…';
        try {
            const res = await fetch(`${API}/signals/${card.dataset.id}/cancel`,
                                    { method: 'POST', headers: { 'X-CSRFToken': csrf() } });
            const r = await res.json();
            toast(r.success
                ? `Signal stood down — ${r.cancelled_orders} cancelled, `
                  + `${r.exited_positions} squared off`
                : (r.error || 'Stand-down incomplete'), r.success ? 'success' : 'error');
        } catch (e) {
            toast(e.message, 'error');
        }
        state.signalSig = state.bookSig = null;
        loadSignals();
        loadBook();
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

    /**
     * Exit all: cancel what this page has resting, then square off what it is
     * holding. One request — the server does both passes in order, because a
     * resting stop cancelled after the position is closed is a naked entry
     * waiting to trigger.
     *
     * Scoped to this page. The server sizes each exit from this page's own
     * records and caps it at what the broker actually shows open, so an OI
     * Profile position on the same strike, in the same account, is not part of
     * it — and neither is a quantity already closed by hand at the terminal.
     *
     * Two presses, and the first one only arms: this button is beside a Cancel
     * all it does not do the same thing as, and it is the one that reaches
     * positions.
     */
    let exitArmTimer = null;

    async function exitAll(btn) {
        if (!btn._armed) {
            btn._armed = true;
            btn.textContent = 'Confirm exit?';
            btn.classList.add('op-armed');
            clearTimeout(exitArmTimer);
            exitArmTimer = setTimeout(() => {
                btn._armed = false;
                btn.textContent = 'Exit all';
                btn.classList.remove('op-armed');
            }, 3000);
            return;
        }

        btn._armed = false;
        clearTimeout(exitArmTimer);
        btn.classList.remove('op-armed');
        btn.disabled = true;
        btn.textContent = 'Exiting…';

        try {
            const res = await fetch(`${API}/exit-all`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
            });
            const r = await res.json();
            // Counts either way: a partial exit did place orders, and a
            // message that only said "failed" would hide them.
            const parts = [];
            if (r.cancelled_orders) parts.push(`${r.cancelled_orders} cancelled`);
            if (r.exited_positions) parts.push(`${r.exited_positions} squared off`);
            if (!parts.length && r.success) parts.push('nothing open from this page');
            const errs = r.errors || (r.error ? [r.error] : []);
            toast(`Exit: ${parts.join(', ')}`
                  + (errs.length ? `\n• ${errs.slice(0, 3).join('\n• ')}` : ''),
                  r.success ? 'success' : 'error');
        } catch (e) {
            toast(`Exit error: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Exit all';
            state.bookSig = null;
            loadBook();
        }
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

        const reread = () => state.scheduleSignalRead && state.scheduleSignalRead();

        segment('opOptionType', v => {
            state.optionType = v;
            state.ltp = null;
            $('opLtpHint').textContent = '';
            renderContract();
            disarm();
            reread();
        });
        segment('opAction', v => {
            state.action = v;
            // A stop reads as the opposite thing on the opposite side, so the
            // hint under the trigger has to follow the side.
            renderPriceField();
            disarm();
            reread();
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

        // Both presses route by mode. Neither review() nor reviewSignal() can
        // reach a broker; only the second press does, and only through the one
        // sender its own mode owns.
        $('opPlace').addEventListener('click',
            () => (state.mode === 'signal' ? reviewSignal() : review()));
        $('opConfirmCancel').addEventListener('click', () => { disarm(); setMsg(''); });
        $('opConfirmSend').addEventListener('click',
            () => (state.mode === 'signal' ? sendSignal() : send()));

        segment('opMode', setMode);

        // Debounced: every keystroke otherwise costs a quote and a chain read,
        // against a request budget shared with the chart feeds.
        let readTimer = null;
        const scheduleRead = () => {
            clearTimeout(readTimer);
            readTimer = setTimeout(readSignal, 350);
        };
        state.scheduleSignalRead = scheduleRead;
        [SIG_FIELDS.entry, SIG_FIELDS.stop, ...SIG_TARGETS].forEach(id =>
            $(id).addEventListener('input', () => { disarm(); scheduleRead(); }));
        // The contract is half the ticket. Changing the strike or the side has
        // to re-run the checks too, or the pad goes on showing a plan for the
        // contract it was on a moment ago.
        $('opStrike').addEventListener('input', scheduleRead);
        $('opSymbol').addEventListener('change', scheduleRead);

        $('opSignals').addEventListener('click', (e) => {
            const btn = e.target.closest('.op-sig-x');
            if (btn) standDown(btn);
        });

        // Enter never places an order: it only ever gets as far as the review
        // bar, which is the same first press the button gives.
        $('opPlace').closest('.op-pad').addEventListener('keydown', (e) => {
            if (e.key !== 'Enter') return;
            if (e.target.closest('.op-confirm')) return;
            e.preventDefault();
            if (state.mode === 'signal') reviewSignal(); else review();
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
        $('opExitAll').addEventListener('click', (e) => exitAll(e.currentTarget));

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
            state.bookSig = state.signalSig = null;
            loadConfig();
            loadContract();
            loadBook();
            loadSignals();
        });

        // Paints the default mode's fields and disarms — the markup already
        // ships in this state, so this is about state, not a redraw.
        setMode(state.mode);
        loadConfig().then(loadContract);
        loadBook();
        loadSignals();
        setInterval(() => {
            if (document.hidden) return;
            loadBook();
            loadSignals();
        }, POLL_MS);
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
