/**
 * multichart.js — one symbol, one to four timeframes, live.
 *
 * The Lightweight Charts panes share a symbol, an indicator set (mine_cpr.js)
 * and ONE poll: every tick fetches today's 1-minute bars once and each pane
 * re-buckets them into its own timeframe — 09:15-anchored, so 1-min → 5/60
 * is exact — and patches its forming bar with series.update(). Every pane
 * live, one broker request per tick.
 *
 * The chart count (1-4) is a toolbar control, remembered per browser along
 * with the timeframes each count was last left with. TF_PRESETS is where a
 * count starts from: 1 → 5m, 2 → 1m + 1h, 3 → 1m + 5m + 1h, 4 → + 1D.
 *
 * Bars are on the app's fake-IST grid (IST clock as UTC seconds), hence the
 * `timezone: 'Etc/UTC'` and the UTC getters in every date split.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const STORE_KEY = 'multichart-v1';
    const MAX_PANES = 4;
    const DEFAULT_COUNT = 2;
    // What each chart count opens at. A pane's own dropdown overrides its
    // entry and the override is kept under that count (see save()), so
    // 2 → 3 → 2 comes back to the timeframes you had, not to these.
    const TF_PRESETS = {
        1: ['5minute'],
        2: ['minute', '60minute'],
        3: ['minute', '5minute', '60minute'],
        4: ['minute', '5minute', '60minute', 'day'],
    };
    const TF_OPTIONS = [
        ['minute', '1m'], ['2minute', '2m'], ['3minute', '3m'], ['5minute', '5m'], ['10minute', '10m'],
        ['15minute', '15m'], ['30minute', '30m'], ['60minute', '1h'], ['day', '1D'],
    ];
    const TF_LABEL = Object.fromEntries(TF_OPTIONS);
    const POLL_MS = { open: 2000, hidden: 10000, closed: 60000, error: 5000 };
    const REFRESH_MS = 5 * 60 * 1000;          // full re-fetch of every pane, heals gaps
    const HEAL_MS = 20000;                     // and no faster than this after a failed load
    const RIGHT_OFFSET = 12;                   // bars of whitespace — Future CPR lives there
    const INITIAL_BARS = 80;                   // bars on screen after a load — the zoom the charts open at
    const SPLIT_PX = 6;                        // the drag handle between two panes, also their spacing
    const MIN_PANE = 140;                      // a pane can be dragged no narrower / shorter than this
    const MIN_HEIGHT = 200, MAX_HEIGHT = 4000; // the grid height the bottom handle can set
    const stackedMQ = window.matchMedia('(max-width: 900px)');   // one column, panes down the page
    const isStacked = () => stackedMQ.matches;

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
        count: DEFAULT_COUNT,      // how many panes
        tfs: TF_PRESETS[DEFAULT_COUNT].slice(),   // the live count's timeframes, one per pane
        tfsBy: {},                 // count -> the timeframes that count was last left with
        settings: {},
        maximised: null,
        // Track shares per layout ('row:3', 'stack:2', 'quad:cols', 'quad:rows'),
        // each summing to 1; a missing key means equal panes.
        shares: {},
        height: null,      // grid height in px set by the bottom handle; null = fit the window
        symbols: [],
        panes: [],
        marketOpen: null,
        prevClose: null,
        contract: 'spot',          // what the last load actually charted
        contractLabel: '',         // 'SEPFUT' — the resolved contract, for the pane title
        futureSymbol: '',          // 'NSE:NIFTY26SEPFUT' — the key today's trade tape is kept under
        loadSeq: 0,
        pollTimer: null,
        pollAbort: null,
        lastRefresh: 0,
        lastHeal: 0,
        hoverPane: null,
    };

    /* ── persistence ─────────────────────────────────────────────────────── */
    function restore() {
        try {
            const raw = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
            if (raw.symbol) state.symbol = String(raw.symbol).toUpperCase();
            if (raw.settings && typeof raw.settings === 'object') state.settings = raw.settings;
            if (TF_PRESETS[raw.count]) state.count = Number(raw.count);
            // One entry per count. A saved shape from before the count control
            // (a bare array of three) has no key that matches, so it is simply
            // dropped and the presets stand.
            if (raw.tfs && typeof raw.tfs === 'object') {
                for (const n of Object.keys(raw.tfs)) {
                    if (TF_PRESETS[n] && validTfs(raw.tfs[n], Number(n))) state.tfsBy[n] = raw.tfs[n].slice();
                }
            }
            if (raw.shares && typeof raw.shares === 'object') {
                for (const k of Object.keys(raw.shares)) if (validShares(raw.shares[k])) state.shares[k] = raw.shares[k].slice();
            }
            if (Number.isInteger(raw.maximised) && raw.maximised >= 0 && raw.maximised < state.count) state.maximised = raw.maximised;
            if (Number.isFinite(raw.height) && raw.height >= MIN_HEIGHT) state.height = Math.min(raw.height, MAX_HEIGHT);
        } catch (e) { /* first visit or blocked storage */ }
        state.tfs = tfsFor(state.count);
    }
    const validTfs = (a, n) => Array.isArray(a) && a.length === n && a.every(t => TF_LABEL[t]);
    const validShares = a => Array.isArray(a) && a.length >= 2 && a.length <= MAX_PANES
        && a.every(v => Number.isFinite(v) && v > 0);
    const tfsFor = n => (state.tfsBy[n] || TF_PRESETS[n] || TF_PRESETS[DEFAULT_COUNT]).slice();
    function save() {
        state.tfsBy[state.count] = state.tfs.slice();   // so the count remembers its dropdowns
        try {
            localStorage.setItem(STORE_KEY, JSON.stringify({
                symbol: state.symbol, count: state.count, tfs: state.tfsBy,
                settings: state.settings, maximised: state.maximised,
                shares: state.shares, height: state.height,
            }));
        } catch (e) { /* storage blocked — the page still works */ }
    }
    // Page settings that are not Pine inputs. `tpoPane` is which pane the TPO
    // profile is drawn on ('all', or a pane index as a string) — a Multichart
    // question, since it is the only page with more than one chart, so it
    // lives here rather than in the shared MineTPO.SPEC.
    const PAGE_DEFAULTS = { countdown: true, futVolume: true, futChart: false, tpoPane: 'all', ofPane: 'all' };

    // A per-browser view preference, kept apart from the chart state above
    // so it never rides along with a symbol/timeframe save. Guarded like the
    // rest: blocked storage must not stop the page rendering.
    const CHROME_KEY = 'mc.hideChrome';
    function readChrome() {
        try { return localStorage.getItem(CHROME_KEY) === '1'; } catch (e) { return false; }
    }
    function applyChrome(hidden) {
        document.body.classList.toggle('mc-bare', hidden);
        const btn = $('mcChrome');
        const label = hidden ? 'Show the nav bar' : 'Hide the nav bar on this screen';
        btn.textContent = hidden ? '▾' : '▴';
        btn.setAttribute('aria-pressed', String(hidden));
        btn.setAttribute('aria-label', label);
        btn.title = label;
    }
    function initChrome() {
        applyChrome(readChrome());
        $('mcChrome').addEventListener('click', () => {
            const hidden = !document.body.classList.contains('mc-bare');
            applyChrome(hidden);
            try { localStorage.setItem(CHROME_KEY, hidden ? '1' : '0'); } catch (e) { /* not fatal */ }
            fitGrid();   // the grid's height is measured from its top, which just moved
        });
    }
    const setting = key => (key in state.settings) ? state.settings[key]
        : (key in PAGE_DEFAULTS) ? PAGE_DEFAULTS[key]
        : (key in MineTPO.DEFAULTS) ? MineTPO.DEFAULTS[key]
        : (key in MineOrderFlow.DEFAULTS) ? MineOrderFlow.DEFAULTS[key] : MineCPR.DEFAULTS[key];

    // Spot or the current-expiry future — the page's data source, sent with
    // every read so the candles, the daily CPR rows and the TPO profile all
    // come off the same instrument. A root with no listed contract falls back
    // to spot server-side and says so in `source`.
    const dataSource = () => (setting('futChart') ? 'future' : 'spot');

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

    // Same banner with something to click. `msg` stays SHORT: the banner is
    // one ellipsised line capped at half the viewport, and a long message
    // truncates the link away — which is the one part that has to be
    // reachable. The explanation goes in the tooltip instead.
    function bannerLink(msg, href, label, title) {
        banner(msg);
        const el = $('mcBanner');
        const a = document.createElement('a');
        a.href = href; a.textContent = label; a.className = 'mc-banner-link';
        el.append(' ', a);
        el.title = title || msg;
    }

    // TWO different logins can 401 this page and the server sends
    // `auth_required` for both, so the message is what tells them apart.
    // The app's own session is the common one: the LaunchAgent respawns this
    // app several times a session and a restart drops it — saying "Fyers
    // login required" then sends you to the wrong page.
    function authBanner(e) {
        if (/user authentication|login first/i.test(e.message || '')) {
            bannerLink('Session expired.', '/auth/user-login', 'Sign in',
                       'The app restarted, which drops the browser session. Sign in again and the charts reload.');
        } else {
            banner('Fyers login required — log in on the Login page, then reload.');
        }
    }

    // A failure worth another go: the fetch itself never landed (the page was
    // open across an app restart — the LaunchAgent is back in ~15s), or the
    // server answered 5xx. A 4xx is an answer, not a blip, so it is not
    // retried. The waits are the restart's own shape: serving again at ~8s.
    const RETRY_MS = [1500, 4000, 8000];
    const transient = e => !e.status || e.status >= 500;
    const sleep = ms => new Promise(r => setTimeout(r, ms));

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
            tpoPrimitive: null, tpoCache: new Map(),   // one entry per finished session
            ofPrimitive: null, ofCache: new Map(),     // footprints, likewise one entry per session
            futVol: new Map(),        // bar time -> future volume, on this pane's grid
            titleEl: node.querySelector('.mc-pane-title'), ohlcEl: node.querySelector('.mc-ohlc'),
            cprEl: node.querySelector('.mc-pane-cpr'), tpoEl: node.querySelector('.mc-pane-tpo'),
            ofEl: node.querySelector('.mc-pane-of'), sel,
        };

        sel.addEventListener('change', () => {
            pane.tf = sel.value; state.tfs[index] = sel.value;
            pane.tpoCache.clear(); pane.ofCache.clear(); save();
            chart.timeScale().applyOptions({ timeVisible: pane.tf !== 'day' });
            loadPane(pane, ++pane.seq);
            refreshGateTags();
            refreshPaneOptions();
        });
        node.querySelector('.mc-max').addEventListener('click', () => toggleMax(index));
        node.querySelector('.mc-pane-head').addEventListener('dblclick', e => { if (e.target === pane.titleEl || e.target.classList.contains('mc-pane-head')) toggleMax(index); });

        chart.subscribeCrosshairMove(param => {
            const bar = param && param.seriesData && param.seriesData.get(series);
            showOhlc(pane, bar || lastBar(pane));
        });
        pane.countdown = TradingViewChart.attachCountdown(chart, series, {
            interval: () => pane.tf, lastBar: () => lastBar(pane), countdown: () => setting('countdown'),
            upColor: UP, downColor: DOWN,
        });
        pane.seq = 0;
        linkCrosshair(pane);
        return pane;
    }

    // Undoing buildPane. Two things outlive the chart on their own: the
    // countdown primitive, held in a module-level set behind a 1-second timer,
    // and the footprint's stats-pane sizing poll — both would go on ticking
    // against a chart that is gone.
    function destroyPane(pane) {
        pane.seq++;                                    // a load in flight lands on nothing
        try { if (pane.countdown) pane.countdown.detach(); } catch (e) { /* already gone */ }
        try { MineOrderFlow.detach(pane); } catch (e) { /* never drew one */ }
        try { pane.chart.remove(); } catch (e) { /* already gone */ }
        pane.node.remove();
    }

    const lastBar = pane => pane.candles.length ? pane.candles[pane.candles.length - 1] : null;

    // What the panes are actually charting. `state.contract` is the broker
    // symbol the last load resolved to, so a futures chart names its contract
    // rather than quietly showing the index's name over the future's bars.
    function paneSymbol() {
        if (state.contract !== 'future') return state.symbol;
        return `${state.symbol} ${state.contractLabel || 'FUT'}`;
    }

    // A futures read that the server had to serve from spot (no listed
    // contract, or the symbol master would not answer) is worth saying out
    // loud — the levels on screen are not the ones being traded.
    function setContract(body) {
        state.contract = body.source || 'spot';
        // Sent whether the chart is on spot or the future, and it is what the
        // trade tape — and so the footprint — is collected under.
        state.futureSymbol = body.future_symbol || '';
        // 'NSE:NIFTY25SEPFUT' -> 'SEPFUT'; the root is already in the title.
        const m = /([A-Z]{3}FUT)$/.exec(body.fy_symbol || '');
        state.contractLabel = m ? m[1] : 'FUT';
        if (body.source_requested === 'future' && body.source !== 'future') {
            banner(`No listed future for ${state.symbol} — charting spot instead.`);
        }
        for (const p of state.panes) p.titleEl.textContent = `${paneSymbol()} · ${TF_LABEL[p.tf]}`;
    }

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
        applyTpo(pane);
        applyOrderFlow(pane);
    }

    // The TPO profile is its own engine and its own primitive, drawn under the
    // same candles. Finished sessions are memoised in pane.tpoCache, so a live
    // tick only rebuilds the developing one.
    function applyTpo(pane) {
        if (!tpoWanted(pane)) {                    // another pane owns the profile
            MineTPO.attach(pane, { profiles: [] });
            pane.tpoEl.textContent = '';
            return;
        }
        const tpo = MineTPO.compute(pane.candles, pane.tf, state.settings, pane.tpoCache);
        MineTPO.attach(pane, tpo);
        pane.tpoEl.textContent = tpo.profiles.length
            ? `TPO ${TF_LABEL[tpoSizeFor(pane)] || ''} · ${setting('tpoVA')}%` : '';
    }

    // 'all' draws it everywhere; otherwise only the pane whose index was picked.
    function tpoWanted(pane) {
        const target = setting('tpoPane');
        return target === 'all' || String(pane.index) === String(target);
    }

    // The period a pane actually draws: the chosen size, or the pane's own
    // timeframe when that is coarser (a 1h pane cannot show 30-minute letters).
    function tpoSizeFor(pane) {
        const want = MineTPO.periodSecs(pane.tf, state.settings);
        return TF_OPTIONS.reduce((best, [v]) => MineCPR.SECONDS[v] === want ? v : best, pane.tf);
    }

    /* ── order flow ──────────────────────────────────────────────────────── */
    // The footprint is the only indicator here with a data source of its own:
    // the app's trade tape, which is collected on the FRONT-MONTH FUTURE and
    // nothing else. Spot candles sit a basis away from those prints, so a
    // spot pane is told to switch rather than shown cells against prices that
    // never traded on it — turning the box on does the switch (see the popup's
    // onChange), and this is the guard for every other way of getting here.
    //
    // Asking the tape for the days on screen is what drives the fetch: today's
    // is polled on the seq cursor by the same live tick that patches the bars,
    // and an earlier session is read out of the archive once.
    function applyOrderFlow(pane) {
        // The footprint's data source is the trade tape, which is collected on
        // the FRONT-MONTH FUTURE and nothing else. This page can chart that
        // instrument, so it is the one page that draws the price cells — and
        // ticking the box on switches the source (see the popup's onChange).
        // A spot pane gets nothing rather than cells against prices that never
        // traded on it.
        const on = setting('of') && ofWanted(pane) && state.contract === 'future';
        if (setting('of') && ofWanted(pane) && state.contract !== 'future') {
            MineOrderFlow.apply(pane, pane.candles, pane.tf, state.settings, { on: false });
            candleStyle(pane, 'normal');
            setOfLabel(pane, 'OF · needs Fut',
                       'The trade tape is collected on the future — switch the data source to Fut.');
            return;
        }
        const out = MineOrderFlow.apply(pane, pane.candles, pane.tf, state.settings, {
            on, root: state.symbol, futureSymbol: state.futureSymbol, cells: true,
        });
        // The candle only makes way for cells that are actually drawn: with the
        // footprint hidden there is nothing to sit between, so it goes back to
        // the chart's own full-width one.
        candleStyle(pane, out.result.bars.length && setting('ofFootprint')
            ? setting('ofCandle') : 'normal');
        setOfLabel(pane, out.text, out.title);
    }

    function setOfLabel(pane, text, title) {
        pane.ofEl.textContent = text;
        pane.ofEl.title = title || '';
    }

    // 'all' draws it everywhere; otherwise only the pane whose index was picked.
    function ofWanted(pane) {
        const target = setting('ofPane');
        return target === 'all' || String(pane.index) === String(target);
    }

    // How this pane's candles are painted while a footprint is on them.
    //
    // The series cannot be drawn narrower than a bar, and the footprint
    // primitive is at zOrder 'bottom', so at full width a solid body covers
    // the figures inside it. 'narrow' answers that the way footprint charts
    // do: the SERIES is painted transparent here and MineOrderFlow draws a
    // thin candle down the middle of the cells instead. The series still holds
    // the data, so the crosshair, the OHLC readout, the price line and the
    // countdown are all untouched — it is only not painted.
    //
    // Restored in full the moment the pane stops drawing cells, so a chart
    // without order flow looks exactly as it did.
    const CANDLE_STYLES = {
        normal: { upColor: UP, downColor: DOWN, borderVisible: false,
                  wickUpColor: UP, wickDownColor: DOWN },
        full:   { upColor: UP, downColor: DOWN, borderVisible: false,
                  wickUpColor: UP, wickDownColor: DOWN },
        hollow: { upColor: 'transparent', downColor: 'transparent', borderVisible: true,
                  borderUpColor: UP, borderDownColor: DOWN,
                  wickUpColor: UP, wickDownColor: DOWN },
        narrow: { upColor: 'transparent', downColor: 'transparent', borderVisible: false,
                  wickUpColor: 'transparent', wickDownColor: 'transparent' },
    };

    function candleStyle(pane, mode) {
        if (pane.candleMode === mode) return;
        pane.candleMode = mode;
        pane.series.applyOptions(CANDLE_STYLES[mode] || CANDLE_STYLES.normal);
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
        pane.titleEl.textContent = `${paneSymbol()} · ${TF_LABEL[pane.tf]}`;
        try {
            for (let attempt = 0; ; attempt++) {
                try {
                    const body = await getJSON(`/api/multichart/candles?symbol=${encodeURIComponent(state.symbol)}&interval=${pane.tf}&source=${dataSource()}`);
                    if (seq !== pane.seq) return;                 // a newer load superseded this one
                    setContract(body);
                    pane.daily = body.daily || [];
                    pane.futVol = new Map((body.future_volume || []).map(v => [v.time, v.volume]));
                    setCandles(pane, body.candles || [], true);
                    if (!body.candles || !body.candles.length) {
                        pane.node.classList.add('empty');
                        pane.el.dataset.empty = body.fetch_error ? `no data — ${body.fetch_error}` : 'no data';
                    }
                    updatePrevClose(pane.daily);
                    banner('');
                    return;
                } catch (e) {
                    if (e.name === 'AbortError' || seq !== pane.seq) return;
                    if (transient(e) && attempt < RETRY_MS.length) {
                        pane.el.dataset.empty = `${e.message} — retrying…`;
                        banner(`${e.message} — retrying…`);
                        await sleep(RETRY_MS[attempt]);
                        if (seq !== pane.seq) return;
                        continue;
                    }
                    pane.node.classList.add('empty');
                    pane.el.dataset.empty = e.message;
                    if (e.authRequired) authBanner(e);
                    else banner(`Load failed: ${e.message}`);
                    return;
                }
            }
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
            setLive('closed', `${new Date().toLocaleTimeString('en-IN', { hour12: false })}`);
            schedule(POLL_MS.closed);
            return;
        }
        const ctrl = new AbortController();
        state.pollAbort = ctrl;
        const symbol = state.symbol;
        let next = POLL_MS.error;
        try {
            const body = await getJSON(`/api/multichart/live?symbol=${encodeURIComponent(symbol)}&source=${dataSource()}`, ctrl.signal);
            if (symbol !== state.symbol) return;          // symbol changed mid-flight; the new one rescheduled
            state.marketOpen = !!body.market_open;
            for (const pane of state.panes) {
                mergeLive(pane, body.candles || []);
                mergeVolume(pane, body.future_volume || []);
                paintVolume(pane);
            }
            renderQuote(body.ltp);
            const stamp = new Date().toLocaleTimeString('en-IN', { hour12: false });
            if (state.marketOpen) setLive('open', `${stamp}`);
            else setLive('closed', `${stamp}`);   // server says closed (holiday it knows, we don't)
            if (body.fetch_error && !(body.candles || []).length) banner(`Live: ${body.fetch_error}`);
            next = document.hidden ? POLL_MS.hidden : (state.marketOpen ? POLL_MS.open : POLL_MS.closed);
            // A pane the server was down for is still empty; this tick proves
            // the server is answering again, so reload it rather than leave a
            // dead chart until someone presses F5. Rate-limited, or a pane
            // that is legitimately empty (a symbol with no data) would refetch
            // every two seconds for the rest of the session.
            const dead = state.panes.some(p => p.node.classList.contains('empty'));
            if (dead && Date.now() - state.lastHeal > HEAL_MS) { state.lastHeal = Date.now(); loadAll(); }
            else if (Date.now() - state.lastRefresh > REFRESH_MS) loadAll();
        } catch (e) {
            if (e.name === 'AbortError') return;
            setLive('error', e.authRequired ? 'login required' : 'feed error');
            if (e.authRequired) authBanner(e);
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
    // Drawn by TradingViewChart.attachCountdown (components/tradingview-chart.js),
    // the shared price + countdown block every chart in the app uses. Each
    // pane hands it its own forming bar and timeframe; the setting only
    // switches the countdown row.

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
    // Set up by buildPane, so a pane added by the count control is linked to
    // the rest the moment it exists. The flag is module-level for the same
    // reason: the panes it guards are rebuilt, the guard is not.
    let crossSyncing = false;
    function linkCrosshair(pane) {
        // Only the pane under the pointer drives the others. Every live tick
        // makes a chart re-fire its crosshair event, and a pane that was merely
        // being synced would otherwise answer by pushing the hovered pane's
        // line to the bar close — the crosshair "jumping to the candle" on
        // each update.
        pane.el.addEventListener('pointerenter', () => { state.hoverPane = pane.index; });
        pane.el.addEventListener('pointerleave', () => { if (state.hoverPane === pane.index) state.hoverPane = null; });

        pane.chart.subscribeCrosshairMove(param => {
            if (crossSyncing || state.hoverPane !== pane.index) return;
            crossSyncing = true;
            try {
                const when = typeof param.time === 'number' ? param.time : null;
                // The horizontal line carries the hovered PRICE across the
                // panes — same instrument, same axis — not each bar's close.
                const price = param.point ? pane.series.coordinateToPrice(param.point.y) : null;
                for (const other of state.panes) {
                    if (other === pane) continue;
                    const bar = when != null ? barAt(other, when) : null;
                    if (!bar) other.chart.clearCrosshairPosition();
                    else other.chart.setCrosshairPosition(price != null ? price : bar.close, bar.time, other.series);
                }
            } finally { crossSyncing = false; }
        });
    }

    /* ── maximise one pane ───────────────────────────────────────────────── */
    function toggleMax(index) {
        state.maximised = state.maximised === index ? null : index;
        $('mcGrid').classList.toggle('max', state.maximised !== null);
        state.panes.forEach(p => p.node.classList.toggle('maxed', p.index === state.maximised));
        save();
        applyLayout();
        fitGrid();
    }

    /* ── resizable layout ────────────────────────────────────────────────── */
    // Height: the bottom handle's value if it has been dragged, else the rest
    // of the window. The charts are autoSize, so a pane that changes size
    // redraws itself.
    function fitGrid() {
        const grid = $('mcGrid');
        if (state.height !== null) { grid.style.height = `${state.height}px`; return; }
        const top = grid.getBoundingClientRect().top;
        const handle = ($('mcHSplit') || {}).offsetHeight || 0;
        grid.style.height = `${Math.max(360, window.innerHeight - top - handle - 8)}px`;
    }
    window.addEventListener('resize', fitGrid);

    // How the panes sit: one row side by side, a 2x2 block for four charts —
    // four in a row is too narrow to read anything off — or one column down
    // the page on a narrow screen, whatever the count.
    function gridMode() {
        if (isStacked()) return 'stack';
        return state.count === MAX_PANES ? 'quad' : 'row';
    }
    // The shares for one axis of one mode, or equal tracks if it has never
    // been dragged. Kept per mode so a 2x2 split and a 3-across split can
    // both be remembered, and switching back finds each as it was left.
    function shareTracks(key, n) {
        const s = state.shares[key];
        const f = (Array.isArray(s) && s.length === n) ? s : Array(n).fill(1 / n);
        return f.map(v => `minmax(${MIN_PANE}px, ${v}fr)`).join(` ${SPLIT_PX}px `);
    }

    // Tracks: pane, handle, pane, handle, pane — across the row on a wide
    // screen, down the column when stacked, both ways for the 2x2. The shares
    // are fractions of the pane space (the handles are fixed), so a window
    // resize keeps the split. A maximised pane leaves the templates to the
    // .max rule.
    function applyLayout() {
        const grid = $('mcGrid');
        const mode = gridMode();
        grid.classList.toggle('stacked', mode === 'stack');
        grid.classList.toggle('quad', mode === 'quad');
        placePanes(mode);
        if (state.maximised !== null) { grid.style.gridTemplateColumns = ''; grid.style.gridTemplateRows = ''; return; }
        const n = state.count;
        if (mode === 'quad') {
            grid.style.gridTemplateColumns = shareTracks('quad:cols', 2);
            grid.style.gridTemplateRows = shareTracks('quad:rows', 2);
        } else if (mode === 'stack') {
            grid.style.gridTemplateColumns = '1fr';
            grid.style.gridTemplateRows = shareTracks(`stack:${n}`, n);
        } else {
            grid.style.gridTemplateColumns = shareTracks(`row:${n}`, n);
            grid.style.gridTemplateRows = '1fr';
        }
    }

    // Only the 2x2 needs saying where anything goes: each of its two handles
    // spans the whole grid (one down the middle, one across it), so DOM-order
    // placement would not put the panes where they belong. Every other mode
    // is pane/handle/pane in order, which grid does by itself.
    function placePanes(mode) {
        const quad = mode === 'quad' && state.maximised === null;
        state.panes.forEach((p, i) => {
            p.node.style.gridColumn = quad ? ((i % 2) ? '3' : '1') : '';
            p.node.style.gridRow = quad ? ((i > 1) ? '3' : '1') : '';
        });
    }

    // Both halves of a layout change: the tracks, and the handles that drag
    // them. The handle set itself depends on the mode, so it is rebuilt.
    function relayout() { applyLayout(); rebuildSplitters(); }
    stackedMQ.addEventListener('change', relayout);

    // Shared drag loop: capture the pointer on the handle, feed each move's
    // pixel delta to `onMove`, and save once on release. Charts under the
    // pointer are switched off for the drag (.resizing) so a fast move over
    // a canvas cannot start a pan.
    function drag(e, bar, onMove) {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        e.preventDefault();
        const grid = $('mcGrid');
        const x0 = e.clientX, y0 = e.clientY;
        grid.classList.add('resizing'); bar.classList.add('active');
        bar.setPointerCapture(e.pointerId);
        const move = ev => onMove(ev.clientX - x0, ev.clientY - y0);
        const end = () => {
            bar.removeEventListener('pointermove', move);
            bar.removeEventListener('pointerup', end);
            bar.removeEventListener('pointercancel', end);
            grid.classList.remove('resizing'); bar.classList.remove('active');
            save();
        };
        bar.addEventListener('pointermove', move);
        bar.addEventListener('pointerup', end);
        bar.addEventListener('pointercancel', end);
    }

    // The handles for the current mode: one between each pair of panes in a
    // row or a column, and for the 2x2 one down the middle and one across —
    // each of those moving a whole column / row, so the block stays a block.
    // Rebuilt rather than reused, since the mode decides how many there are.
    function rebuildSplitters() {
        const grid = $('mcGrid');
        for (const el of grid.querySelectorAll('.mc-split')) el.remove();
        const mode = gridMode();
        if (state.count < 2) return;                   // one chart, nothing to split
        if (mode === 'quad') {
            makeSplit({ key: 'quad:cols', axis: 'col', idx: 0, tracks: () => [0, 1],
                        place: { gridColumn: '2', gridRow: '1 / -1' } });
            makeSplit({ key: 'quad:rows', axis: 'row', idx: 0, tracks: () => [0, 2],
                        place: { gridColumn: '1 / -1', gridRow: '2' } });
            return;
        }
        const axis = mode === 'stack' ? 'row' : 'col';
        const key = `${mode}:${state.count}`;
        const all = () => state.panes.map((p, i) => i);
        for (let k = 0; k < state.count - 1; k++) {
            makeSplit({ key, axis, idx: k, tracks: all, after: state.panes[k].node });
        }
    }

    // One handle. `tracks` names the panes whose boxes measure each track —
    // every pane in a row or column, the two of a 2x2 axis — and `idx` which
    // neighbouring pair the drag trades size between; the rest are left alone
    // and the pair is clamped at MIN_PANE each. Double-click makes the tracks
    // of that axis equal again.
    function makeSplit(opts) {
        const horz = opts.axis === 'row';
        const bar = document.createElement('div');
        bar.className = `mc-split ${horz ? 'horz' : 'vert'}`;
        bar.setAttribute('role', 'separator');
        bar.title = 'Drag to resize · double-click for equal panes';
        if (opts.place) Object.assign(bar.style, opts.place);
        if (opts.after) opts.after.after(bar); else $('mcGrid').appendChild(bar);
        bar.addEventListener('dblclick', () => {
            delete state.shares[opts.key];
            save(); applyLayout();
        });
        bar.addEventListener('pointerdown', e => {
            const sizes = opts.tracks().map(i => {
                const r = state.panes[i].node.getBoundingClientRect();
                return horz ? r.height : r.width;
            });
            const k = opts.idx, a0 = sizes[k], b0 = sizes[k + 1];
            drag(e, bar, (dx, dy) => {
                const d = Math.max(MIN_PANE - a0, Math.min(b0 - MIN_PANE, horz ? dy : dx));
                const next = sizes.slice(); next[k] = a0 + d; next[k + 1] = b0 - d;
                const total = next.reduce((s, v) => s + v, 0);
                state.shares[opts.key] = next.map(v => v / total);
                applyLayout();
            });
        });
    }

    // The bar under the grid sets its height; taller than the window scrolls
    // the page. Double-click goes back to filling the window.
    function initHeightHandle() {
        const bar = document.createElement('div');
        bar.className = 'mc-hsplit'; bar.id = 'mcHSplit';
        bar.setAttribute('role', 'separator');
        bar.title = 'Drag to set the chart height · double-click to fit the window';
        $('mcGrid').after(bar);
        bar.addEventListener('dblclick', () => { state.height = null; save(); fitGrid(); });
        bar.addEventListener('pointerdown', e => {
            const h0 = $('mcGrid').getBoundingClientRect().height;
            drag(e, bar, (dx, dy) => {
                state.height = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.round(h0 + dy)));
                fitGrid();
            });
        });
    }

    /* ── chart count ─────────────────────────────────────────────────────── */
    // Changing the count rebuilds the panes: a chart cannot be moved into a
    // new grid slot, and a new pane needs its own series, primitives and
    // session caches. save() puts the outgoing count's timeframes away first,
    // so coming back to it finds the dropdowns it was left with.
    function setCount(n) {
        n = Number(n);
        if (!TF_PRESETS[n] || n === state.count) return;
        save();
        state.count = n;
        state.tfs = tfsFor(n);
        state.maximised = null;             // the pane it pointed at may be gone
        state.hoverPane = null;
        $('mcGrid').classList.remove('max');
        for (const pane of state.panes) destroyPane(pane);
        state.panes = [];
        for (let i = 0; i < n; i++) state.panes.push(buildPane(i));
        // A "draw on" target that no longer has a pane would draw nowhere.
        for (const key of ['tpoPane', 'ofPane']) {
            const v = setting(key);
            if (v !== 'all' && !(Number(v) < n)) state.settings[key] = 'all';
        }
        save();
        syncCountUI();
        refreshPaneOptions();
        relayout();
        fitGrid();
        loadAll();
        refreshGateTags();
    }

    function syncCountUI() {
        for (const btn of document.querySelectorAll('#mcCount .mc-src-btn')) {
            const on = Number(btn.dataset.count) === state.count;
            btn.classList.toggle('on', on);
            btn.setAttribute('aria-pressed', String(on));
        }
    }

    function initCount() {
        $('mcCount').addEventListener('click', e => {
            const btn = e.target.closest('.mc-src-btn');
            if (btn) setCount(btn.dataset.count);
        });
        syncCountUI();
    }

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
        // Another instrument: another price grid, and another tape entirely.
        for (const p of state.panes) { p.tpoCache.clear(); p.ofCache.clear(); }
        MineOrderFlow.Tape.forget();
        save();
        document.title = `${symbol} · Multichart`;
        renderQuote(null);
        loadAll();
        schedule(0);
    }

    /* ── indicators popup ────────────────────────────────────────────────── */
    // The Mine CPR list itself (MineCPR.SPEC, rendered by MineCPR.renderSettings)
    // is shared with the OI Profile main chart; this page prepends its own
    // 'Chart' section for the two settings that are not Pine inputs.
    const PAGE_SPEC = [
        { title: 'Chart', items: [
            { key: 'futChart', label: 'Chart the future (current expiry)' },
            { key: 'futVolume', label: 'Future volume (current expiry)', color: UP },
            { key: 'countdown', label: 'Bar-close countdown on price axis' },
        ] },
    ];

    /* ── data source: spot / future ──────────────────────────────────────── */
    // The one path that flips it, whichever control was used. It is a
    // different instrument, not a drawing option, so every pane refetches and
    // every memoised profile goes with it.
    function setDataSource(useFuture) {
        if (setting('futChart') === useFuture) return;
        state.settings.futChart = useFuture;
        save();
        syncSourceUI();
        setDataSourceFromSetting();
    }

    // Keeps the toolbar switch and the popup's row showing the same thing.
    // bindSettings has already written state.settings by the time its
    // onChange runs, so the popup's row only needs the reload half.
    function setDataSourceFromSetting() {
        for (const pane of state.panes) pane.tpoCache.clear();
        state.prevClose = null;
        banner('');
        loadAll();
        schedule(0);
    }

    function syncSourceUI() {
        const useFuture = !!setting('futChart');
        for (const btn of document.querySelectorAll('#mcSrc .mc-src-btn')) {
            const on = (btn.dataset.src === 'future') === useFuture;
            btn.classList.toggle('on', on);
            btn.setAttribute('aria-pressed', String(on));
        }
        const cb = document.querySelector('#mcIndPopup input[data-key="futChart"]');
        if (cb) cb.checked = useFuture;
    }

    function initSource() {
        $('mcSrc').addEventListener('click', e => {
            const btn = e.target.closest('.mc-src-btn');
            if (btn) setDataSource(btn.dataset.src === 'future');
        });
        syncSourceUI();
    }

    // Built at render time so the options name each pane's current timeframe;
    // refreshPaneLabels() keeps them honest when one is changed.
    const tpoPaneOptions = () => [['all', 'All charts']].concat(
        state.tfs.map((tf, i) => [String(i), `Chart ${i + 1} · ${TF_LABEL[tf]}`]));

    const TPO_PANE_SPEC = () => [
        { title: 'TPO chart', items: [
            { key: 'tpoPane', type: 'select', label: 'Draw TPO on', options: tpoPaneOptions() },
        ] },
    ];

    // Same question for the footprint, and for the same reason it is a page
    // setting rather than a MineOrderFlow one: Multichart is the only page
    // with more than one chart. A footprint is the densest thing drawn here,
    // so picking one pane for it is the normal way to use it.
    const OF_PANE_SPEC = () => [
        { title: 'Order flow chart', items: [
            { key: 'ofPane', type: 'select', label: 'Draw order flow on', options: tpoPaneOptions() },
        ] },
    ];

    // Rebuilt, not relabelled: the count decides how many rows these two
    // selects have, so a pane appearing or going away changes the list.
    function refreshPaneOptions() {
        const opts = tpoPaneOptions();
        for (const key of ['tpoPane', 'ofPane']) {
            const sel = document.querySelector(`#mcIndPopup select[data-key="${key}"]`);
            if (!sel) continue;
            const current = String(setting(key));
            sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
            sel.value = opts.some(([v]) => v === current) ? current : 'all';
        }
    }

    function buildIndicatorsPopup() {
        const popup = $('mcIndPopup');
        popup.innerHTML = '<div class="mc-ind-head">Indicators · Mine CPR</div>' +
            MineCPR.renderSettings(setting, PAGE_SPEC.concat(MineCPR.SPEC, MineTPO.SPEC, TPO_PANE_SPEC(),
                                                             MineOrderFlow.SPEC, OF_PANE_SPEC()));

        MineCPR.bindSettings(popup, (key, v) => { state.settings[key] = v; save(); },
            key => {
                // A TPO input changes the rows themselves, so every memoised
                // session has to go — the developing one alone is not enough.
                if (key in MineTPO.DEFAULTS) for (const pane of state.panes) pane.tpoCache.clear();
                // Every order-flow input moves the cells themselves, so the
                // memoised sessions go with it.
                if (key in MineOrderFlow.DEFAULTS) for (const pane of state.panes) pane.ofCache.clear();
                if (key === 'futChart') { syncSourceUI(); setDataSourceFromSetting(); return; }
                // The footprint can only be drawn against the contract the
                // tape was collected on, so turning it on takes the charts
                // there rather than leaving the user with an empty indicator.
                if (key === 'of' && v && !setting('futChart')) {
                    banner('Order flow reads the futures trade tape — charting the current-expiry future.');
                    setDataSource(true);
                    return;
                }
                // Switching the target pane means one pane starts drawing and
                // another stops: both need the pass, so nothing is left behind.
                if (key === 'tpoPane') { for (const pane of state.panes) applyTpo(pane); return; }
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
        // `ofCells` lights the footprint row's tag: this page can chart the
        // future the tape is collected on, so the cells are available here
        // whenever it is doing so.
        MineCPR.refreshGates($('mcIndPopup'),
            state.panes.map(p => Object.assign({}, MineCPR.tfInfo(p.tf), MineTPO.tfInfo(p.tf),
                                               MineOrderFlow.tfInfo(p.tf),
                                               { ofCells: state.contract === 'future' })));
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
        initChrome();   // before fitGrid: the nav's height decides where the grid starts
        document.title = `${state.symbol} · Multichart`;
        for (let i = 0; i < state.count; i++) state.panes.push(buildPane(i));
        initCount();
        initHeightHandle();
        if (state.maximised !== null) { const m = state.maximised; state.maximised = null; toggleMax(m); }
        relayout();
        fitGrid();
        initPicker();
        // The tape is fetched asynchronously and shared by every pane, so a
        // day landing after the candles did has to repaint them itself —
        // nothing else would call applyOrderFlow until the next live tick.
        MineOrderFlow.Tape.setListener(() => { for (const pane of state.panes) applyOrderFlow(pane); });
        buildIndicatorsPopup();
        initSource();          // after the popup: it syncs that row's checkbox
        loadAll();
        schedule(0);
        loadSymbols();
    }

    // Read-only handle for the console / harness checks (pane candles, tfs, poll state).
    window.MultichartDebug = state;

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
