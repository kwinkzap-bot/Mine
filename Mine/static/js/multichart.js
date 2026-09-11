/**
 * multichart.js — one symbol, four timeframes, live.
 *
 * Four Lightweight Charts panes share a symbol, an indicator set (mine_cpr.js)
 * and ONE poll: every tick fetches today's 1-minute bars once and each pane
 * re-buckets them into its own timeframe — 09:15-anchored, so 1-min → 3/5/60
 * is exact — and patches its forming bar with series.update(). Four live
 * charts, one broker request per tick.
 *
 * Bars are on the app's fake-IST grid (IST clock as UTC seconds), hence the
 * `timezone: 'Etc/UTC'` and the UTC getters in every date split.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const STORE_KEY = 'multichart-v1';
    const DEFAULT_TFS = ['minute', '3minute', '5minute', '60minute'];
    const TF_OPTIONS = [
        ['minute', '1m'], ['2minute', '2m'], ['3minute', '3m'], ['5minute', '5m'], ['10minute', '10m'],
        ['15minute', '15m'], ['30minute', '30m'], ['60minute', '1h'], ['day', '1D'],
    ];
    const TF_LABEL = Object.fromEntries(TF_OPTIONS);
    const POLL_MS = { open: 2000, hidden: 10000, closed: 60000, error: 5000 };
    const REFRESH_MS = 5 * 60 * 1000;          // full re-fetch of every pane, heals gaps
    const RIGHT_OFFSET = 12;                   // bars of whitespace — Future CPR lives there
    const INITIAL_BARS = 80;                   // bars on screen after a load — the zoom the charts open at

    const CHART_THEMES = {
        light:  { bg: '#ffffff', text: '#374151', grid: '#f0f0f0' },
        dark:   { bg: '#111827', text: '#94a3b8', grid: 'rgba(255,255,255,0.06)' },
        forest: { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16,185,129,0.06)' },
        cream:  { bg: '#ffffff', text: '#7c7267', grid: 'rgba(180,83,9,0.05)' },
        ocean:  { bg: '#ffffff', text: '#475569', grid: 'rgba(2,132,199,0.05)' },
    };
    const UP = '#1b9981', DOWN = '#f23645';      // candle / volume / axis-tag colours
    const ANCHOR_LABEL = { day: 'Daily CPR', week: 'Weekly CPR', month: 'Monthly CPR', year: 'Yearly CPR' };

    const state = {
        symbol: 'NIFTY',
        tfs: DEFAULT_TFS.slice(),
        settings: {},
        maximised: null,
        symbols: [],
        panes: [],
        marketOpen: null,
        prevClose: null,
        loadSeq: 0,
        pollTimer: null,
        pollAbort: null,
        lastRefresh: 0,
        hoverPane: null,
    };

    /* ── persistence ─────────────────────────────────────────────────────── */
    function restore() {
        try {
            const raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
            if (raw.symbol) state.symbol = String(raw.symbol).toUpperCase();
            if (Array.isArray(raw.tfs) && raw.tfs.length === 4 && raw.tfs.every(t => TF_LABEL[t])) state.tfs = raw.tfs;
            if (raw.settings && typeof raw.settings === 'object') state.settings = raw.settings;
            if (Number.isInteger(raw.maximised)) state.maximised = raw.maximised;
        } catch (e) { /* first visit or blocked storage */ }
    }
    function save() {
        try {
            localStorage.setItem(STORE_KEY, JSON.stringify({
                symbol: state.symbol, tfs: state.tfs, settings: state.settings, maximised: state.maximised,
            }));
        } catch (e) { /* storage blocked — the page still works */ }
    }
    const PAGE_DEFAULTS = { countdown: true, futVolume: true };   // page settings that are not Pine inputs
    const setting = key => (key in state.settings) ? state.settings[key]
        : (key in PAGE_DEFAULTS) ? PAGE_DEFAULTS[key] : MineCPR.DEFAULTS[key];

    /* ── fetch helpers ───────────────────────────────────────────────────── */
    async function getJSON(url, signal) {
        const r = await fetch(url, { signal, credentials: 'same-origin' });
        let body = null;
        try { body = await r.json(); } catch (e) { /* non-JSON error page */ }
        if (!r.ok || !body || body.success === false) {
            const err = new Error((body && body.error) || `HTTP ${r.status}`);
            err.status = r.status;
            err.authRequired = !!(body && body.auth_required);
            throw err;
        }
        return body;
    }

    function banner(msg) {
        const el = $('mcBanner');
        if (!msg) { el.hidden = true; el.textContent = ''; return; }
        el.textContent = msg; el.title = msg; el.hidden = false;
    }

    /* ── panes ───────────────────────────────────────────────────────────── */
    function themeCfg() {
        const t = (window.AppTheme && window.AppTheme.getActiveTheme()) || 'ocean';
        return CHART_THEMES[t] || CHART_THEMES.ocean;
    }

    function chartLayout() {
        const th = themeCfg();
        return {
            layout: { background: { type: 'solid', color: th.bg }, textColor: th.text, fontSize: 10, attributionLogo: false },
            grid: { vertLines: { color: th.grid }, horzLines: { color: th.grid } },
        };
    }

    function buildPane(index) {
        const node = $('mcPaneTpl').content.firstElementChild.cloneNode(true);
        $('mcGrid').appendChild(node);
        const sel = node.querySelector('.mc-tf');
        for (const [v, label] of TF_OPTIONS) {
            const o = document.createElement('option'); o.value = v; o.textContent = label; sel.appendChild(o);
        }
        sel.value = state.tfs[index];

        const el = node.querySelector('.mc-chart');
        const chart = LightweightCharts.createChart(el, Object.assign({
            autoSize: true,
            rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
            timeScale: { borderVisible: false, timeVisible: state.tfs[index] !== 'day', secondsVisible: false,
                         rightOffset: RIGHT_OFFSET, barSpacing: 8, minBarSpacing: 1 },
            localization: { timeFormatter: window.lwCrosshairTime, timezone: 'Etc/UTC', priceFormatter: fmt },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            handleScale: { axisPressedMouseMove: true },
        }, chartLayout()));
        const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
            // The last-value tag is drawn by the countdown primitive instead,
            // so the price and the countdown share one box.
            priceLineVisible: true, lastValueVisible: false,
        });
        // Volume of the current-expiry future, in the bottom fifth on its own
        // hidden scale — the index has no volume of its own, and reading the
        // future's for a stock too keeps every symbol on the same footing.
        const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
            priceFormat: { type: 'volume' }, priceScaleId: 'vol',
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
        });
        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
        if (window.TradingViewChart && TradingViewChart.addScrollButton) TradingViewChart.addScrollButton(chart, series, el);

        const pane = {
            index, node, chart, series, volume, el,
            tf: state.tfs[index], candles: [], daily: [], lines: {}, primitive: null,
            futVol: new Map(),        // bar time -> future volume, on this pane's grid
            titleEl: node.querySelector('.mc-pane-title'), ohlcEl: node.querySelector('.mc-ohlc'),
            cprEl: node.querySelector('.mc-pane-cpr'), sel,
        };

        sel.addEventListener('change', () => {
            pane.tf = sel.value; state.tfs[index] = sel.value; save();
            chart.timeScale().applyOptions({ timeVisible: pane.tf !== 'day' });
            loadPane(pane, ++pane.seq);
            refreshGateTags();
        });
        node.querySelector('.mc-max').addEventListener('click', () => toggleMax(index));
        node.querySelector('.mc-pane-head').addEventListener('dblclick', e => { if (e.target === pane.titleEl || e.target.classList.contains('mc-pane-head')) toggleMax(index); });

        chart.subscribeCrosshairMove(param => {
            const bar = param && param.seriesData && param.seriesData.get(series);
            showOhlc(pane, bar || lastBar(pane));
        });
        pane.countdown = makeCountdownPrimitive(pane);
        series.attachPrimitive(pane.countdown);
        pane.seq = 0;
        return pane;
    }

    const lastBar = pane => pane.candles.length ? pane.candles[pane.candles.length - 1] : null;

    function fmt(v) {
        if (v == null || !isFinite(v)) return '—';
        return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function showOhlc(pane, bar) {
        if (!bar) { pane.ohlcEl.textContent = ''; return; }
        const dir = bar.close >= bar.open ? 'up' : 'dn';
        pane.ohlcEl.className = `mc-ohlc ${dir}`;
        pane.ohlcEl.innerHTML = `<b>O</b>${fmt(bar.open)} <b>H</b>${fmt(bar.high)} <b>L</b>${fmt(bar.low)} <b>C</b>${fmt(bar.close)}`;
    }

    function fitWithRightPad(chart, count) {
        if (!count) return;
        const shown = Math.min(count, INITIAL_BARS);
        chart.timeScale().setVisibleLogicalRange({ from: count - shown - 0.5, to: count - 0.5 + RIGHT_OFFSET });
    }

    function setCandles(pane, candles, fit) {
        pane.candles = candles;
        pane.series.setData(candles);
        if (fit) fitWithRightPad(pane.chart, candles.length);
        applyIndicators(pane);
        paintVolume(pane);
        showOhlc(pane, lastBar(pane));
    }

    function applyIndicators(pane) {
        const result = MineCPR.compute(pane.candles, pane.tf, pane.daily, state.settings);
        MineCPR.attach(pane, result);
        pane.cprEl.textContent = setting('cpr') && result.anchor ? (ANCHOR_LABEL[result.anchor] || '') : '';
    }

    // Histogram bars tinted by the spot candle's direction; blank when off.
    function paintVolume(pane) {
        if (!setting('futVolume') || !pane.futVol.size) { pane.volume.setData([]); return; }
        const bars = [];
        for (const c of pane.candles) {
            const v = pane.futVol.get(c.time);
            if (v == null) continue;
            bars.push({ time: c.time, value: v, color: (c.close >= c.open ? UP : DOWN) + '66' });
        }
        pane.volume.setData(bars);
    }

    // Today's 1-minute future volume → this pane's buckets (summed).
    function mergeVolume(pane, minuteVol) {
        if (!minuteVol.length) return;
        const secs = pane.tf === 'day' ? 86400 : MineCPR.SECONDS[pane.tf];
        const t0 = minuteVol[0].time, dayT = t0 - (t0 % 86400);
        const sums = new Map();
        for (const v of minuteVol) {
            const key = pane.tf === 'day' ? dayT : MineCPR.sessionStart(v.time) + Math.floor((v.time - MineCPR.sessionStart(v.time)) / secs) * secs;
            sums.set(key, (sums.get(key) || 0) + (v.volume || 0));
        }
        for (const [t, v] of sums) pane.futVol.set(t, v);
    }

    async function loadPane(pane, seq) {
        pane.node.classList.add('loading');
        pane.node.classList.remove('empty');
        pane.titleEl.textContent = `${state.symbol} · ${TF_LABEL[pane.tf]}`;
        try {
            const body = await getJSON(`/api/multichart/candles?symbol=${encodeURIComponent(state.symbol)}&interval=${pane.tf}`);
            if (seq !== pane.seq) return;                 // a newer load superseded this one
            pane.daily = body.daily || [];
            pane.futVol = new Map((body.future_volume || []).map(v => [v.time, v.volume]));
            setCandles(pane, body.candles || [], true);
            if (!body.candles || !body.candles.length) {
                pane.node.classList.add('empty');
                pane.el.dataset.empty = body.fetch_error ? `no data — ${body.fetch_error}` : 'no data';
            }
            updatePrevClose(pane.daily);
            banner('');
        } catch (e) {
            if (e.name === 'AbortError' || seq !== pane.seq) return;
            pane.node.classList.add('empty');
            pane.el.dataset.empty = e.message;
            if (e.authRequired) banner('Fyers login required — log in on the Login page, then reload.');
            else banner(`Load failed: ${e.message}`);
        } finally {
            if (seq === pane.seq) pane.node.classList.remove('loading');
        }
    }

    function loadAll() {
        state.lastRefresh = Date.now();
        for (const pane of state.panes) loadPane(pane, ++pane.seq);
    }

    /* ── toolbar quote ───────────────────────────────────────────────────── */
    function updatePrevClose(daily) {
        if (!daily || daily.length < 2) return;
        const today = MineCPR.dayKey(Math.floor(Date.now() / 1000) + 19800);
        // The last daily row dated before today; the final row may be today's running bar.
        for (let i = daily.length - 1; i >= 0; i--) {
            if (daily[i].date < today) { state.prevClose = daily[i].c; break; }
        }
        renderQuote();
    }

    function renderQuote(ltp) {
        if (ltp == null) {
            const p = state.panes.find(p => p.tf !== 'day' && p.candles.length) || state.panes.find(p => p.candles.length);
            ltp = p ? lastBar(p).close : null;
        }
        $('mcLtp').textContent = fmt(ltp);
        const chg = $('mcChg');
        if (ltp != null && state.prevClose) {
            const d = ltp - state.prevClose, pct = d / state.prevClose * 100;
            chg.textContent = `${d >= 0 ? '▲' : '▼'} ${fmt(Math.abs(d))} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
            chg.className = `mc-chg ${d >= 0 ? 'up' : 'dn'}`;
        } else { chg.textContent = ''; chg.className = 'mc-chg'; }
    }

    /* ── live loop ───────────────────────────────────────────────────────── */
    function setLive(cls, text) {
        const el = $('mcLive');
        el.className = `mc-live ${cls}`;
        $('mcLiveText').textContent = text;
    }

    function schedule(ms) {
        clearTimeout(state.pollTimer);
        state.pollTimer = setTimeout(tick, ms);
    }

    // NSE hours in IST, holidays included (common.js); a local fallback for
    // the harness, which does not load it. Nothing is fetched while closed.
    function marketOpenNow() {
        if (typeof window.isMarketOpen === 'function') return window.isMarketOpen();
        const t = Math.floor(Date.now() / 1000) + 19800, d = new Date(t * 1000);
        const dow = d.getUTCDay(), mins = d.getUTCHours() * 60 + d.getUTCMinutes();
        return dow >= 1 && dow <= 5 && mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
    }

    async function tick() {
        if (state.pollAbort) state.pollAbort.abort();
        // Outside market hours there is nothing live to fetch: no /live call,
        // no 5-minute history refresh — just a local re-check each minute so
        // the loop wakes itself at the open.
        if (!marketOpenNow()) {
            state.marketOpen = false;
            setLive('closed', `closed · ${new Date().toLocaleTimeString('en-IN', { hour12: false })}`);
            schedule(POLL_MS.closed);
            return;
        }
        const ctrl = new AbortController();
        state.pollAbort = ctrl;
        const symbol = state.symbol;
        let next = POLL_MS.error;
        try {
            const body = await getJSON(`/api/multichart/live?symbol=${encodeURIComponent(symbol)}`, ctrl.signal);
            if (symbol !== state.symbol) return;          // symbol changed mid-flight; the new one rescheduled
            state.marketOpen = !!body.market_open;
            for (const pane of state.panes) {
                mergeLive(pane, body.candles || []);
                mergeVolume(pane, body.future_volume || []);
                paintVolume(pane);
            }
            renderQuote(body.ltp);
            const stamp = new Date().toLocaleTimeString('en-IN', { hour12: false });
            if (state.marketOpen) setLive('open', `live ${stamp}`);
            else setLive('closed', `closed · ${stamp}`);   // server says closed (holiday it knows, we don't)
            if (body.fetch_error && !(body.candles || []).length) banner(`Live: ${body.fetch_error}`);
            next = document.hidden ? POLL_MS.hidden : (state.marketOpen ? POLL_MS.open : POLL_MS.closed);
            if (Date.now() - state.lastRefresh > REFRESH_MS) loadAll();
        } catch (e) {
            if (e.name === 'AbortError') return;
            setLive('error', e.authRequired ? 'login required' : 'feed error');
            if (e.authRequired) banner('Fyers login required — log in on the Login page, then reload.');
            next = e.authRequired ? POLL_MS.closed : POLL_MS.error;
        }
        schedule(next);
    }

    // Today's 1-minute bars → this pane's timeframe, then patch the tail.
    function mergeLive(pane, minuteBars) {
        if (!minuteBars.length || !pane.candles.length) return;
        let today;
        if (pane.tf === 'day') {
            const t0 = minuteBars[0].time;
            today = [{
                time: t0 - (t0 % 86400), open: minuteBars[0].open,
                high: Math.max(...minuteBars.map(b => b.high)), low: Math.min(...minuteBars.map(b => b.low)),
                close: minuteBars[minuteBars.length - 1].close,
                volume: minuteBars.reduce((s, b) => s + (b.volume || 0), 0),
            }];
        } else {
            today = MineCPR.aggregate(minuteBars, MineCPR.SECONDS[pane.tf]);
        }
        const firstT = today[0].time;
        const old = pane.candles;
        let cut = old.length;
        while (cut > 0 && old[cut - 1].time >= firstT) cut--;
        if (cut === 0 && old[0].time > firstT) return;   // history is from a later day than the feed — stale symbol

        const oldToday = old.slice(cut);
        const merged = old.slice(0, cut).concat(today);
        const same = (a, b) => a && b && a.time === b.time && a.open === b.open && a.high === b.high && a.low === b.low && a.close === b.close;
        // series.update() may only touch the last bar or append one; anything
        // else (a finished bar revised, a gap healed) is a full setData.
        let onlyTail = today.length >= oldToday.length && today.length - oldToday.length <= 1;
        for (let i = 0; onlyTail && i < oldToday.length - 1; i++) if (!same(oldToday[i], today[i])) onlyTail = false;
        if (onlyTail && oldToday.length && today.length > oldToday.length && !same(oldToday[oldToday.length - 1], today[oldToday.length - 1])) onlyTail = false;

        pane.candles = merged;
        if (onlyTail) {
            const last = today[today.length - 1];
            if (!same(last, oldToday[oldToday.length - 1])) pane.series.update(last);
        } else {
            pane.series.setData(merged);
        }
        applyIndicators(pane);
        showOhlc(pane, lastBar(pane));
    }

    document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });

    /* ── bar-close countdown on the price axis ───────────────────────────── */
    // TradingView's "00:25" under the last price: seconds until this pane's
    // forming bar closes. A v5 series primitive can hand the price axis a
    // label of its own, so it sits directly beneath the built-in last-value
    // tag in the same colour and reads as one block. Refreshed once a second
    // while the bar is live; blank between sessions.
    const SESSION_OPEN_S = 9 * 3600 + 15 * 60, SESSION_CLOSE_S = 15 * 3600 + 30 * 60;
    const nowIst = () => Math.floor(Date.now() / 1000) + 19800;

    function barCloseAt(pane) {
        const b = lastBar(pane);
        if (!b) return null;
        const day = b.time - (b.time % 86400);
        if (pane.tf === 'day') return day + SESSION_CLOSE_S;
        return Math.min(b.time + MineCPR.SECONDS[pane.tf], day + SESSION_CLOSE_S);
    }

    function countdownText(pane) {
        const b = lastBar(pane), close = barCloseAt(pane), now = nowIst();
        if (!b || close == null || now < b.time || now >= close) return '';
        const left = close - now;
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), sec = left % 60;
        const p2 = n => String(n).padStart(2, '0');
        return h ? `${h}:${p2(m)}:${p2(sec)}` : `${p2(m)}:${p2(sec)}`;
    }

    function makeCountdownPrimitive(pane) {
        let requestUpdate = null, price = '', text = '';
        // Both rows are ours — the series' own last-value tag is switched off
        // — so they share one colour and one width and read as a single block,
        // the way TradingView draws it. The countdown is padded with figure
        // spaces (digit-width) to the price's digit count so its box matches.
        const y = () => { const b = lastBar(pane); const c = b && pane.series.priceToCoordinate(b.close); return c == null ? -100 : c; };
        const back = () => { const b = lastBar(pane); return b && b.close < b.open ? DOWN : UP; };
        const priceView = {
            coordinate: y, text: () => price, textColor: () => '#ffffff', backColor: back,
            visible: () => !!price, tickVisible: () => true,
        };
        const countView = {
            coordinate: () => y() + 16, text: () => text, textColor: () => '#ffffff', backColor: back,
            visible: () => !!text, tickVisible: () => false,
        };
        return {
            attached(p) { requestUpdate = p.requestUpdate; },
            detached() { requestUpdate = null; },
            updateAllViews() {
                const b = lastBar(pane);
                price = b ? pane.series.priceFormatter().format(b.close) : '';
                const cd = setting('countdown') ? countdownText(pane) : '';
                const pad = Math.max(0, price.length - cd.length);
                text = cd ? '\u2007'.repeat(Math.ceil(pad / 2)) + cd + '\u2007'.repeat(Math.floor(pad / 2)) : '';
            },
            priceAxisViews: () => [priceView, countView],
            paneViews: () => [],
            refresh() { if (requestUpdate) requestUpdate(); },
        };
    }

    setInterval(() => { for (const pane of state.panes) if (pane.countdown) pane.countdown.refresh(); }, 1000);

    /* ── crosshair link across timeframes ────────────────────────────────── */
    // The bar of `pane` containing `when` — the last one that had started by
    // then — so hovering 10:31 on the 1m pane lights the 10:30 bar on 3m and
    // the 10:15 bar on 1h.
    function barAt(pane, when) {
        const c = pane.candles;
        if (!c.length || when == null || when < c[0].time) return null;
        let lo = 0, hi = c.length - 1;
        while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (c[mid].time <= when) lo = mid; else hi = mid - 1; }
        return c[lo];
    }
    function linkCrosshairs() {
        let syncing = false;
        state.panes.forEach((pane, i) => {
            // Only the pane under the pointer drives the others. Every live
            // tick makes a chart re-fire its crosshair event, and a pane that
            // was merely being synced would otherwise answer by pushing the
            // hovered pane's line to the bar close — the crosshair "jumping to
            // the candle" on each update.
            pane.el.addEventListener('pointerenter', () => { state.hoverPane = i; });
            pane.el.addEventListener('pointerleave', () => { if (state.hoverPane === i) state.hoverPane = null; });

            pane.chart.subscribeCrosshairMove(param => {
                if (syncing || state.hoverPane !== i) return;
                syncing = true;
                try {
                    const when = typeof param.time === 'number' ? param.time : null;
                    // The horizontal line carries the hovered PRICE across the
                    // panes — same instrument, same axis — not each bar's close.
                    const price = param.point ? pane.series.coordinateToPrice(param.point.y) : null;
                    state.panes.forEach((other, j) => {
                        if (j === i) return;
                        const bar = when != null ? barAt(other, when) : null;
                        if (!bar) other.chart.clearCrosshairPosition();
                        else other.chart.setCrosshairPosition(price != null ? price : bar.close, bar.time, other.series);
                    });
                } finally { syncing = false; }
            });
        });
    }

    /* ── maximise one pane ───────────────────────────────────────────────── */
    function toggleMax(index) {
        state.maximised = state.maximised === index ? null : index;
        $('mcGrid').classList.toggle('max', state.maximised !== null);
        state.panes.forEach(p => p.node.classList.toggle('maxed', p.index === state.maximised));
        save();
        fitGrid();
    }

    function fitGrid() {
        const grid = $('mcGrid');
        const top = grid.getBoundingClientRect().top;
        grid.style.height = `${Math.max(360, window.innerHeight - top - 8)}px`;
    }
    window.addEventListener('resize', fitGrid);

    /* ── symbol picker ───────────────────────────────────────────────────── */
    async function loadSymbols() {
        try {
            const body = await getJSON('/api/multichart/symbols');
            state.symbols = body.symbols || [];
        } catch (e) {
            state.symbols = [{ symbol: 'NIFTY', kind: 'INDEX' }, { symbol: 'BANKNIFTY', kind: 'INDEX' }];
            if (e.authRequired) banner('Fyers login required — log in on the Login page, then reload.');
        }
    }

    function matches(q) {
        q = q.trim().toUpperCase();
        if (!q) return state.symbols.slice(0, 40);
        const pre = [], sub = [];
        for (const s of state.symbols) {
            if (s.symbol.startsWith(q)) pre.push(s);
            else if (s.symbol.includes(q)) sub.push(s);
        }
        return pre.concat(sub).slice(0, 40);
    }

    function initPicker() {
        const input = $('mcSymbol'), box = $('mcSuggest');
        let active = -1, items = [];
        input.value = state.symbol;

        const close = () => { box.hidden = true; box.innerHTML = ''; active = -1; items = []; };
        const render = () => {
            items = matches(input.value);
            box.innerHTML = items.length
                ? items.map((s, i) => `<button type="button" class="mc-sug${i === active ? ' active' : ''}" data-i="${i}"><span class="mc-sug-sym">${s.symbol}</span><span class="mc-sug-kind">${s.kind}</span></button>`).join('')
                : '<div class="mc-sug-empty">No match</div>';
            box.hidden = false;
        };
        const choose = s => { close(); input.value = s.symbol; input.blur(); selectSymbol(s.symbol); };

        input.addEventListener('focus', () => { input.select(); render(); });
        input.addEventListener('input', () => { active = -1; render(); });
        input.addEventListener('keydown', e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); if (box.hidden) render(); active = Math.min(active + 1, items.length - 1); render(); scrollActive(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); render(); scrollActive(); }
            else if (e.key === 'Enter') {
                e.preventDefault();
                const pick = items[active] || items.find(s => s.symbol === input.value.trim().toUpperCase()) || items[0];
                if (pick) choose(pick);
            }
            else if (e.key === 'Escape') { close(); input.value = state.symbol; input.blur(); }
        });
        const scrollActive = () => { const el = box.querySelector('.mc-sug.active'); if (el) el.scrollIntoView({ block: 'nearest' }); };
        box.addEventListener('mousedown', e => {
            const b = e.target.closest('.mc-sug');
            if (b) { e.preventDefault(); choose(items[+b.dataset.i]); }
        });
        document.addEventListener('mousedown', e => {
            if (!e.target.closest('.mc-search-wrap')) { if (!box.hidden) { close(); input.value = state.symbol; } }
        });
        document.addEventListener('keydown', e => {
            if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
        });
    }

    function selectSymbol(symbol) {
        symbol = symbol.toUpperCase();
        if (symbol === state.symbol) return;
        state.symbol = symbol;
        state.prevClose = null;
        save();
        document.title = `${symbol} · Multichart`;
        renderQuote(null);
        loadAll();
        schedule(0);
    }

    /* ── indicators popup ────────────────────────────────────────────────── */
    // Gates name the tfInfo flag that must hold on a pane for the item to draw
    // there; the tag turns green while any pane qualifies.
    const IND_SPEC = [
        { title: 'Chart', items: [
            { key: 'futVolume', label: 'Future volume (current expiry)', color: UP },
            { key: 'countdown', label: 'Bar-close countdown on price axis' },
        ] },
        { title: 'CPR', items: [
            { key: 'cpr', label: 'CPR — P / BC / TC', color: MineCPR.COLORS.cpr },
            { key: 'shadow', label: 'CPR shadow', sub: true },
            { key: 'kind', type: 'select', label: 'Type', options: [['camarilla', 'Camarilla'], ['traditional', 'Traditional'], ['fibonacci', 'Fibonacci']] },
            { key: 'pivotTf', type: 'select', label: 'Pivots timeframe', options: [['auto', 'Auto'], ['day', 'Daily'], ['week', 'Weekly'], ['month', 'Monthly']] },
            { key: 'pivotsBack', type: 'number', label: 'Pivots back', min: 1, max: 200 },
            { key: 'dailyBased', label: 'Use daily-based values' },
            { key: 'camR3S3', label: 'R3 / S3 (Camarilla)', color: MineCPR.COLORS.cam },
            { row: 'R levels', keys: ['r1', 'r2', 'r3', 'r4'], color: MineCPR.COLORS.r, note: 'Traditional / Fibonacci' },
            { row: 'S levels', keys: ['s1', 's2', 's3', 's4'], color: MineCPR.COLORS.s },
            { key: 'pdhR1Box', label: 'PDH ↔ R1 box', color: MineCPR.COLORS.pdhBox },
            { key: 'pdlS1Box', label: 'PDL ↔ S1 box', color: MineCPR.COLORS.pdlBox },
            { key: 'histPdhl', label: 'PDH / PDL lines', color: MineCPR.COLORS.pdhl },
            { key: 'virgin', label: 'Highlight virgin CPR', color: MineCPR.COLORS.virginFill },
            { key: 'virginExtend', label: 'Extend until touched', sub: true },
            { key: 'futureCpr', label: 'Future CPR (dashed)' },
            { key: 'labels', label: 'Level labels' },
        ] },
        { title: 'Multi CPR', gate: 'showMCPR', gateLabel: '≤15m', items: [
            { key: 'multiCpr', label: 'Multi CPR' },
            { key: 'mcpr15', label: '15 min', sub: true, color: MineCPR.COLORS.mcpr15 },
            { key: 'mcpr30', label: '30 min', sub: true, color: MineCPR.COLORS.mcpr30 },
            { key: 'mcpr60', label: '1 hour', sub: true, color: MineCPR.COLORS.mcpr60 },
        ] },
        { title: 'Moving averages', items: [
            { key: 'emaAll', label: 'Show EMAs' },
            { key: 'ema9', label: 'EMA 9', sub: true, color: MineCPR.COLORS.ema9 },
            { key: 'ema20', label: 'EMA 20', sub: true, color: MineCPR.COLORS.ema20 },
            { key: 'ema50', label: 'EMA 50', sub: true, color: MineCPR.COLORS.ema50 },
            { key: 'ema100', label: 'EMA 100', sub: true, color: MineCPR.COLORS.ema100 },
            { key: 'ema200', label: 'EMA 200', sub: true, color: '#6b7280' },
        ] },
        { title: 'VWAP', gate: 'showVwap', gateLabel: '≤15m', items: [
            { key: 'vwapCur', label: 'Current VWAP', color: MineCPR.COLORS.vwapCur },
            { key: 'vwapPrev', label: 'Previous VWAP', color: MineCPR.COLORS.vwapPrev },
            { key: 'vwapAvg3', label: 'Avg 3 VWAP', color: MineCPR.COLORS.vwapAvg3 },
            { key: 'vwapLabels', label: 'VWAP price labels', sub: true },
        ] },
        { title: 'Boxes', items: [
            { key: 'box5m', label: '2nd 5-min candle box', color: MineCPR.COLORS.box5m, gate: 'show5mBox', gateLabel: '≤5m' },
            { key: 'box1m', label: '2nd 1-min candle box', color: MineCPR.COLORS.box1m, gate: 'show1mBox', gateLabel: '≤1m' },
            { key: 'mondayBox', label: 'Monday H/L box', gate: 'showMonday', gateLabel: '≤1h' },
            { key: 'mondayWeeksBack', type: 'number', label: 'Weeks back', min: 1, max: 500, sub: true },
        ] },
    ];

    function gateTag(flag, label) {
        return flag ? `<span class="mc-gate" data-gate="${flag}">${label}</span>` : '';
    }
    const swatch = c => c ? `<i class="mc-sw" style="background:${c}"></i>` : '';

    function buildIndicatorsPopup() {
        const popup = $('mcIndPopup');
        let html = '<div class="mc-ind-head">Indicators · Mine CPR</div>';
        for (const sec of IND_SPEC) {
            html += `<div class="mc-ind-section"><div class="mc-ind-title">${sec.title}${gateTag(sec.gate, sec.gateLabel)}</div>`;
            for (const it of sec.items) {
                if (it.row) {
                    html += `<div class="mc-ind-row">${swatch(it.color)}<span>${it.row}</span>` +
                        it.keys.map(k => `<label><input type="checkbox" data-key="${k}" ${setting(k) ? 'checked' : ''}>${k.toUpperCase()}</label>`).join('') +
                        `</div>`;
                } else if (it.type === 'select') {
                    html += `<label class="mc-ind-item${it.sub ? ' sub' : ''}"><span>${it.label}</span><select data-key="${it.key}">` +
                        it.options.map(([v, l]) => `<option value="${v}" ${setting(it.key) === v ? 'selected' : ''}>${l}</option>`).join('') +
                        `</select></label>`;
                } else if (it.type === 'number') {
                    html += `<label class="mc-ind-item${it.sub ? ' sub' : ''}"><span>${it.label}</span><input type="number" data-key="${it.key}" min="${it.min}" max="${it.max}" value="${setting(it.key)}"></label>`;
                } else {
                    html += `<label class="mc-ind-item${it.sub ? ' sub' : ''}"><input type="checkbox" data-key="${it.key}" ${setting(it.key) ? 'checked' : ''}>${swatch(it.color)}<span>${it.label}</span>${gateTag(it.gate, it.gateLabel)}</label>`;
                }
            }
            html += '</div>';
        }
        popup.innerHTML = html;

        popup.addEventListener('change', e => {
            const el = e.target, key = el.dataset.key;
            if (!key) return;
            let v;
            if (el.type === 'checkbox') v = el.checked;
            else if (el.type === 'number') { v = Math.max(+el.min, Math.min(+el.max, parseInt(el.value, 10) || +el.min)); el.value = v; }
            else v = el.value;
            state.settings[key] = v;
            save();
            for (const pane of state.panes) { applyIndicators(pane); paintVolume(pane); }
        });

        const btn = $('mcIndBtn');
        btn.addEventListener('click', e => { e.stopPropagation(); popup.hidden = !popup.hidden; btn.classList.toggle('on', !popup.hidden); refreshGateTags(); });
        document.addEventListener('mousedown', e => {
            if (!popup.hidden && !e.target.closest('.mc-ind-wrap')) { popup.hidden = true; btn.classList.remove('on'); }
        });
        document.addEventListener('keydown', e => { if (e.key === 'Escape' && !popup.hidden) { popup.hidden = true; btn.classList.remove('on'); } });
        refreshGateTags();
    }

    function refreshGateTags() {
        const infos = state.panes.map(p => MineCPR.tfInfo(p.tf));
        document.querySelectorAll('#mcIndPopup .mc-gate').forEach(tag => {
            const flag = tag.dataset.gate;
            tag.classList.toggle('live', infos.some(i => i[flag]));
        });
    }

    /* ── theme ───────────────────────────────────────────────────────────── */
    window.addEventListener('themechanged', () => {
        for (const pane of state.panes) {
            pane.chart.applyOptions(chartLayout());
            applyIndicators(pane);        // EMA 200 / Monday box colours follow the theme
        }
    });

    /* ── init ────────────────────────────────────────────────────────────── */
    async function init() {
        if (typeof LightweightCharts === 'undefined') { banner('Chart library failed to load (CDN blocked?)'); return; }
        restore();
        document.title = `${state.symbol} · Multichart`;
        for (let i = 0; i < 4; i++) state.panes.push(buildPane(i));
        if (state.maximised !== null) { const m = state.maximised; state.maximised = null; toggleMax(m); }
        fitGrid();
        linkCrosshairs();
        initPicker();
        buildIndicatorsPopup();
        loadAll();
        schedule(0);
        loadSymbols();
    }

    // Read-only handle for the console / harness checks (pane candles, tfs, poll state).
    window.MultichartDebug = state;

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
