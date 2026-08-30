/* ================================================================
   CandleModal — the two-pane candle popup, shared by the Watchlist grid
   and the Scanner's Camarilla results.

   Markup lives in templates/_candle_modal.html; include that partial and
   link static/css/components/candle-modal.css, then:

       CandleModal.open('RELIANCE', { rows: state.rows });

   `rows` is whatever list the popup was opened from — [{symbol, company,
   tv_symbol}] — and only drives ‹ › stepping and neighbour prefetch. Pass
   the list on screen and stepping walks it in the order the user sees.

   Two panes, because the question "is this a buy" is asked at two
   timeframes at once: the top follows the dropdown, the bottom is pinned
   to weekly so the higher-timeframe structure is always beside it. The
   CPR period is derived from the timeframe rather than picked (see
   INTERVALS in watchlist_service.py) — a 5-minute chart carrying yearly
   pivots is how that goes wrong.

   Extracted from watchlist.js, which owned all of this when the popup had
   one caller.
   ================================================================ */

(function (global) {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const API = '/api/watchlist';

    // The bottom pane is always weekly. It is the reference frame the top
    // pane is read against, so it does not follow the dropdown — otherwise
    // both panes show the same thing and the split buys nothing.
    const WEEKLY = '1wk';

    const INTERVAL_LABELS = {
        '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1h': '1h', '1d': '1D', '1wk': '1W', '1mo': '1M',
    };

    const CACHE_MAX = 40;

    const state = {
        symbol: null,
        rows: [],
        interval: '1d',
        // The CPR period is derived from the timeframe, so this is only
        // whether the overlay is drawn at all.
        cprOn: true,
        // One entry per pane: {chart, series, bars, times}. Disposed
        // together — Lightweight Charts holds a canvas and a resize
        // observer each.
        panes: [],
        // symbol|interval -> payload, and in-flight requests for the same.
        candles: new Map(),
        inflight: new Map(),
        bound: false,
    };

    const CANDLE_THEMES = {
        light:  { bg: '#ffffff', text: '#475569', grid: '#f1f5f9' },
        dark:   { bg: '#111827', text: '#94a3b8', grid: 'rgba(255,255,255,.06)' },
        forest: { bg: '#0a1410', text: '#6ba88f', grid: 'rgba(16,185,129,.06)' },
        cream:  { bg: '#fdf6e9', text: '#7c7267', grid: 'rgba(180,83,9,.05)' },
        ocean:  { bg: '#ffffff', text: '#475569', grid: 'rgba(2,132,199,.05)' },
    };

    // TradingView's own candle colours, which is what the reference chart is
    // showing — not this app's --color-pos/neg, which are tuned for text in
    // a grid and read far heavier as a wall of candle bodies.
    const UP = '#089981';
    const DOWN = '#f23645';

    const escape = (s) => (global.DataGrid ? global.DataGrid.escape(s) : String(s == null ? '' : s));

    async function getJSON(url) {
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        return res.json();
    }

    // Cached per symbol+timeframe and deduped while in flight, so stepping
    // with ‹ › and flicking between timeframes both come back instantly
    // once seen.
    function fetchCandles(symbol, interval) {
        const key = `${symbol}|${interval}`;
        const hit = state.candles.get(key);
        if (hit) return Promise.resolve(hit);
        const pending = state.inflight.get(key);
        if (pending) return pending;

        const request = getJSON(
            `${API}/candles?symbol=${encodeURIComponent(symbol)}&interval=${interval}`)
            .then((data) => {
                if (data && data.success) {
                    // Oldest-first eviction, so a long sitting cannot grow
                    // this without bound.
                    if (state.candles.size >= CACHE_MAX) {
                        state.candles.delete(state.candles.keys().next().value);
                    }
                    state.candles.set(key, data);
                }
                return data;
            })
            .finally(() => state.inflight.delete(key));

        state.inflight.set(key, request);
        return request;
    }

    // Candles the library will accept: it wants every bar to carry all four
    // prices, and a day the source only closed (no OHLC) would otherwise
    // throw and take the whole chart down.
    const toCandles = (points) => (points || [])
        .filter((p) => p.o != null && p.h != null && p.l != null && p.c != null)
        .map((p) => ({ time: p.t, open: p.o, high: p.h, low: p.l, close: p.c }));

    // ── CPR overlay ──────────────────────────────────────────────────
    //
    // CPR (TC / P / BC) with Camarilla R3 / S3, drawn as one canvas
    // primitive attached to the candle series rather than as line series.
    //
    // Two things a line series cannot do, and both of them are the point:
    //  - the band between TC and BC is FILLED, and Lightweight Charts has no
    //    band series;
    //  - each period's levels are separate horizontal shelves. A line series
    //    joins its points, so every period boundary grew a vertical
    //    connector that the reference chart does not have.
    const CPR_STYLE = {
        fill:  'rgba(159, 168, 218, 0.35)',
        edge:  '#3949ab',    // TC and BC — the band's own edges
        pivot: '#1a237e',    // P, darkest: it is the line being read
        cam:   '#8e24aa',    // Camarilla R3 and S3, heavier so the two
                             // indicators stay tellable apart at a glance
        edgeWidth: 1.5,
        pivotWidth: 1.5,
        camWidth: 2,
    };

    // Consecutive bars sharing a period, so the renderer draws one shelf per
    // period rather than one segment per bar.
    //
    // Takes the same candle array the series was given, not payload.points:
    // toCandles() drops any bar missing an OHLC leg, so only this array's
    // positions match the logical indices autoscaleInfo() is handed. Each
    // run therefore carries both its time span (for drawing) and its bar
    // index span (for the autoscale window).
    function periodRuns(candles, periods) {
        if (!periods || !periods.length) return [];
        const runs = [];
        let i = -1;
        let current = null;
        candles.forEach((c, idx) => {
            while (i + 1 < periods.length && periods[i + 1].from <= c.time) {
                i++;
                current = null;              // a new period starts a new shelf
            }
            if (i < 0) return;               // before the first period
            if (!current) {
                current = { levels: periods[i], from: c.time, to: c.time,
                            fromIdx: idx, toIdx: idx };
                runs.push(current);
            } else {
                current.to = c.time;
                current.toIdx = idx;
            }
        });
        return runs;
    }

    function makeCprPrimitive(runs) {
        let series = null;
        let chart = null;

        const renderer = {
            draw(target) {
                if (!series || !chart || !runs.length) return;
                const timeScale = chart.timeScale();
                target.useBitmapCoordinateSpace((scope) => {
                    const ctx = scope.context;
                    const hr = scope.horizontalPixelRatio;
                    const vr = scope.verticalPixelRatio;
                    ctx.save();

                    const lastRun = runs[runs.length - 1];
                    for (const run of runs) {
                        const x1 = timeScale.timeToCoordinate(run.from);
                        const x2 = timeScale.timeToCoordinate(run.to);
                        if (x1 === null || x2 === null) continue;

                        // Half a bar of overhang each side, so a shelf spans
                        // its whole period instead of stopping at the centre
                        // of its first and last candle.
                        const pad = Math.max(1, (timeScale.options().barSpacing || 6) / 2);
                        const left = (x1 - pad) * hr;
                        // The period in progress runs to the right edge: its
                        // levels are the ones still being traded against, and
                        // stopping them at the last candle hides exactly the
                        // part a reader is looking for.
                        const right = run === lastRun
                            ? scope.bitmapSize.width
                            : (x2 + pad) * hr;

                        const y = (price) => {
                            if (price == null) return null;
                            const c = series.priceToCoordinate(price);
                            return c === null ? null : c * vr;
                        };
                        const tc = y(run.levels.tc);
                        const bc = y(run.levels.bc);

                        if (tc !== null && bc !== null) {
                            ctx.fillStyle = CPR_STYLE.fill;
                            ctx.fillRect(left, tc, right - left, bc - tc);
                        }

                        const line = (yy, color, width) => {
                            if (yy === null) return;
                            ctx.beginPath();
                            ctx.strokeStyle = color;
                            ctx.lineWidth = width * vr;
                            ctx.moveTo(left, yy);
                            ctx.lineTo(right, yy);
                            ctx.stroke();
                        };
                        line(tc, CPR_STYLE.edge, CPR_STYLE.edgeWidth);
                        line(bc, CPR_STYLE.edge, CPR_STYLE.edgeWidth);
                        line(y(run.levels.p), CPR_STYLE.pivot, CPR_STYLE.pivotWidth);
                        line(y(run.levels.r3), CPR_STYLE.cam, CPR_STYLE.camWidth);
                        line(y(run.levels.s3), CPR_STYLE.cam, CPR_STYLE.camWidth);
                    }
                    ctx.restore();
                });
            },
        };

        const paneView = {
            renderer: () => renderer,
            // Under the candles: the levels are context to read price
            // against and must not sit on top of the bar being read.
            zOrder: () => 'bottom',
            update: () => {},
        };

        return {
            attached(param) { series = param.series; chart = param.chart; },
            detached() { series = null; chart = null; },
            updateAllViews() {},
            paneViews: () => [paneView],
            // Without this an R3 well above the bars falls off the top of
            // the pane, because the scale only knows about the candles.
            //
            // Only the levels inside [first, last] — the logical bar range on
            // screen. Reporting every period in the payload instead pins the
            // scale to the whole history: with five years loaded and six
            // months shown, a 2021 pivot dragged the range down to 614 while
            // the visible candles sat between 1740 and 2038, leaving them a
            // fifth of the pane height. Harmless while the payload was one
            // year and the view showed all of it; not once it wasn't.
            autoscaleInfo(first, last) {
                let min = Infinity;
                let max = -Infinity;
                for (const run of runs) {
                    // Null range (the library asks for the unbounded case on
                    // some paths) means fall back to every run.
                    if (first != null && run.toIdx < first) continue;
                    if (last != null && run.fromIdx > last) continue;
                    for (const key of ['tc', 'bc', 'p', 'r3', 's3']) {
                        const value = run.levels[key];
                        if (value == null) continue;
                        min = Math.min(min, value);
                        max = Math.max(max, value);
                    }
                }
                return Number.isFinite(min)
                    ? { priceRange: { minValue: min, maxValue: max } } : null;
            },
        };
    }

    function drawCprOverlay(candleSeries, payload, candles) {
        if (!state.cprOn) return;
        const runs = periodRuns(candles || [], payload.levels || []);
        if (!runs.length) return;
        candleSeries.attachPrimitive(makeCprPrimitive(runs));
    }

    // ── panes ────────────────────────────────────────────────────────

    // A bar's time as a Date. Intraday bars are epoch seconds and daily and
    // wider are 'YYYY-MM-DD'; Lightweight Charts also hands back a
    // {year, month, day} object from crosshair events on a business-day
    // series, so all three shapes have to be understood.
    function toDate(time) {
        if (time == null) return null;
        if (typeof time === 'number') return new Date(time * 1000);
        if (typeof time === 'string') return new Date(time + 'T00:00:00');
        if (typeof time === 'object' && time.year) {
            return new Date(time.year, (time.month || 1) - 1, time.day || 1);
        }
        return null;
    }

    // ── IST time labels ──────────────────────────────────────────────
    //
    // Intraday bars are true epoch seconds, and Lightweight Charts renders
    // times in UTC — which puts NSE's 09:15 open on the axis as 03:45 and
    // the 13:15 bar as 07:45. The session is 09:15–15:30 IST, so every
    // intraday label is formatted in Asia/Kolkata instead.
    //
    // Formatted, not shifted: `toDate` and the crosshair link below compare
    // bar times as real instants, and faking the epoch to move the labels
    // would quietly break that. Day boundaries still land correctly because
    // the whole session sits inside one UTC day (03:45–10:00 UTC).
    const IST = 'Asia/Kolkata';
    const istFmt = (opts) => new Intl.DateTimeFormat('en-GB', { timeZone: IST, ...opts });
    const IST_TIME  = istFmt({ hour: '2-digit', minute: '2-digit', hour12: false });
    const IST_DAY   = istFmt({ day: 'numeric' });
    const IST_MONTH = istFmt({ month: 'short' });
    const IST_YEAR  = istFmt({ year: 'numeric' });
    const IST_STAMP = istFmt({ day: '2-digit', month: 'short', year: '2-digit',
                              hour: '2-digit', minute: '2-digit', hour12: false });

    // Axis tick marks. The library picks the granularity and hands it over
    // as a TickMarkType; only the rendering of it changes here.
    function istTickMark(time, tickMarkType) {
        const at = toDate(time);
        if (!at) return '';
        const T = (global.LightweightCharts && global.LightweightCharts.TickMarkType)
            || { Year: 0, Month: 1, DayOfMonth: 2 };
        if (tickMarkType === T.Year) return IST_YEAR.format(at);
        if (tickMarkType === T.Month) return IST_MONTH.format(at);
        if (tickMarkType === T.DayOfMonth) return IST_DAY.format(at);
        return IST_TIME.format(at);
    }

    // The crosshair's time label, in the library's own "18 Aug '26 13:15"
    // shape so only the timezone changes.
    function istStamp(time) {
        const at = toDate(time);
        if (!at) return '';
        const part = {};
        for (const piece of IST_STAMP.formatToParts(at)) part[piece.type] = piece.value;
        return `${part.day} ${part.month} '${part.year} ${part.hour}:${part.minute}`;
    }

    // The bar of `pane` covering `when` — the last one that had started by
    // then. Hovering 10:30 on a 30-minute chart should light up the week
    // that contains it on the weekly pane, not the nearest week boundary.
    //
    // Null when `when` falls outside this pane's data. The two panes need
    // not cover the same span, and clamping to the nearest end instead
    // would park the crosshair on the first bar with a price label
    // attached, reading as if two distant months were the same moment.
    function barAt(pane, when) {
        const times = pane.times;
        if (!times || !times.length || !when) return null;
        if (when < times[0].at) return null;
        let lo = 0;
        let hi = times.length - 1;
        while (lo < hi) {
            const mid = Math.ceil((lo + hi) / 2);
            if (times[mid].at <= when) lo = mid;
            else hi = mid - 1;
        }
        return times[lo];
    }

    // Crosshair sync. Hovering either pane moves the other to the same
    // moment, which is the whole point of showing two timeframes at once —
    // reading them independently means eyeballing which weekly bar the
    // intraday move sits in.
    function linkCrosshairs(panes) {
        const live = panes.filter((p) => p && p.chart && p.series);
        if (live.length < 2) return;
        let syncing = false;

        live.forEach((pane, index) => {
            pane.chart.subscribeCrosshairMove((param) => {
                // Setting the crosshair on the other pane fires its own move
                // event; without this guard the two charts drive each other.
                if (syncing) return;
                syncing = true;
                try {
                    const others = live.filter((_, i) => i !== index);
                    const when = toDate(param.time);
                    for (const other of others) {
                        const bar = when && barAt(other, when);
                        if (!bar) {
                            other.chart.clearCrosshairPosition();
                            continue;
                        }
                        // Priced at that bar's close, so the horizontal arm
                        // lands on the candle rather than wherever the
                        // pointer happened to be in the other pane's scale.
                        other.chart.setCrosshairPosition(bar.close, bar.time, other.series);
                    }
                } finally {
                    syncing = false;
                }
            });
        });
    }

    // Bars of empty space left after the last candle. fitContent() fits the
    // data to the pane exactly, which pins the newest bar against the price
    // scale — the levels projected forward then have nowhere to show, and
    // the last close label sits on top of its own candle.
    const RIGHT_PAD_BARS = 8;

    // How many bars the view OPENS on, at most — not how many were loaded.
    // The daily payload carries five years (~1240 bars); showing all of it
    // at once leaves each candle about a pixel wide, which is a wall rather
    // than a chart. The rest is still loaded and one scroll away.
    //
    // 135 is the zoom the charts are actually read at, taken off a
    // hand-zoomed reference rather than picked: ~8px per bar in a ~1150px
    // pane, which is about six months of daily bars and about two and a
    // half years of weekly ones. Both panes are capped the same, so they
    // stay at a matching candle width — the two are read side by side, and
    // one being visibly denser than the other is what makes them hard to
    // compare.
    const INITIAL_VISIBLE_BARS = 135;

    function fitWithRightPad(chart, barCount) {
        if (!chart || !barCount) return;
        const shown = Math.min(barCount, INITIAL_VISIBLE_BARS);
        // Half a bar of margin each end so neither edge candle is clipped.
        chart.timeScale().setVisibleLogicalRange({
            from: barCount - shown - 0.5,
            to: barCount - 0.5 + RIGHT_PAD_BARS,
        });
    }

    function disposePanes() {
        for (const pane of state.panes) {
            try { pane.chart.remove(); } catch (e) { /* already gone */ }
        }
        state.panes = [];
        $('cmBodyTop').innerHTML = '';
        $('cmBodyWeekly').innerHTML = '';
    }

    function drawPane(containerId, payload) {
        const container = $(containerId);
        container.innerHTML = '';
        const candles = toCandles(payload.points);
        if (!candles.length) {
            container.innerHTML = '<div class="cm-empty">No candles at this timeframe.</div>';
            return null;
        }

        const theme = CANDLE_THEMES[(global.AppTheme && global.AppTheme.getActiveTheme())
            || 'light'] || CANDLE_THEMES.light;
        const chart = LightweightCharts.createChart(container, {
            layout: { textColor: theme.text, background: { type: 'solid', color: theme.bg } },
            grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
            rightPriceScale: {
                borderVisible: false,
                // Price gets ~86% of the pane. Generous margins leave the
                // candles in the middle 60% of the height, which flattens
                // every move — and the headroom they buy is unnecessary now
                // the CPR primitive reports its own levels to the
                // autoscaler.
                scaleMargins: { top: 0.06, bottom: 0.20 },
            },
            // An intraday bar is only identifiable with its time on the axis,
            // and that time has to read in IST — see istTickMark.
            timeScale: { borderVisible: false, timeVisible: !!payload.intraday,
                         secondsVisible: false,
                         tickMarkFormatter: payload.intraday ? istTickMark : undefined },
            localization: payload.intraday ? { locale: 'en-IN', timeFormatter: istStamp }
                                           : { locale: 'en-IN' },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            autoSize: true,
        });
        const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: UP, downColor: DOWN,
            borderUpColor: UP, borderDownColor: DOWN,
            wickUpColor: UP, wickDownColor: DOWN,
        });
        series.setData(candles);

        // Volume in its own scale at the foot of the pane — the standard
        // reading, and it costs nothing since the payload already carries it.
        const volumes = (payload.points || [])
            .filter((p) => p.v != null && p.o != null)
            .map((p) => ({ time: p.t, value: p.v,
                           color: p.c >= p.o ? UP + '4d' : DOWN + '4d' }));
        if (volumes.length) {
            const vol = chart.addSeries(LightweightCharts.HistogramSeries, {
                priceFormat: { type: 'volume' }, priceScaleId: 'vol',
            });
            // Volume in the bottom sixth, out of the price series' way.
            chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
            vol.setData(volumes);
        }

        drawCprOverlay(series, payload, candles);
        fitWithRightPad(chart, candles.length);
        return {
            chart,
            series,
            bars: candles.length,
            // Kept for the crosshair link: the two panes are on different
            // timeframes, so a hovered bar has to be mapped onto whichever
            // bar of the other pane contains the same moment.
            times: candles.map((c) => ({ time: c.time, at: toDate(c.time), close: c.close })),
        };
    }

    // ── load / open / close ──────────────────────────────────────────

    function rowFor(symbol) {
        return state.rows.find((r) => r.symbol === symbol) || { symbol };
    }

    function syncNav() {
        const i = state.rows.findIndex((r) => r.symbol === state.symbol);
        $('cmPrev').disabled = i <= 0;
        $('cmNext').disabled = i < 0 || i >= state.rows.length - 1;
        $('cmInterval').value = state.interval;
        $('cmCpr').classList.toggle('active', state.cprOn);
    }

    async function loadCandles(symbol) {
        const interval = state.interval;
        $('cmBack').classList.add('cm-loading');

        // Both panes in flight together — they are two independent requests
        // and waiting for them in turn doubles the wait for no reason.
        let top, weekly;
        try {
            [top, weekly] = await Promise.all([
                fetchCandles(symbol, interval),
                fetchCandles(symbol, WEEKLY),
            ]);
        } catch (e) {
            top = { success: false, error: e.message };
            weekly = top;
        }
        // Stepped away, or switched timeframe, while this was loading.
        if (state.symbol !== symbol || state.interval !== interval) return;
        $('cmBack').classList.remove('cm-loading');
        disposePanes();

        const paint = (containerId, capId, payload, label) => {
            $(capId).textContent = payload && payload.cpr_period
                ? `${label} · CPR ${payload.cpr_period}` : label;
            if (!payload || !payload.success) {
                $(containerId).innerHTML = `<div class="cm-empty">${escape(
                    (payload && payload.error) || 'No chart data available')}</div>`;
                return null;
            }
            return drawPane(containerId, payload);
        };

        state.panes = [
            paint('cmBodyTop', 'cmCapTop', top, INTERVAL_LABELS[interval] || interval),
            paint('cmBodyWeekly', 'cmCapBottom', weekly, INTERVAL_LABELS[WEEKLY]),
        ].filter(Boolean);
        linkCrosshairs(state.panes);

        // The header button names the timeframe being driven, which is the
        // top pane's; each pane's own caption carries its period too.
        if (top && top.cpr_period) $('cmCprPeriod').textContent = top.cpr_period;
    }

    function open(symbol, opts) {
        if (!symbol || !$('cmBack')) return;
        bind();
        const options = opts || {};
        if (options.rows) state.rows = options.rows;
        if (options.interval) state.interval = options.interval;
        state.symbol = symbol;

        const row = rowFor(symbol);
        const tvSymbol = row.tv_symbol || `NSE:${symbol}`;

        $('cmTitle').innerHTML =
            `<i class="cm-avatar">${escape(symbol.slice(0, 1))}</i>` +
            escape(symbol) +
            `<span class="cm-co">${escape(row.company || '')}</span>`;
        $('cmOut').href =
            `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`;

        $('cmBack').hidden = false;
        syncNav();
        loadCandles(symbol);

        // Prime ‹ › at the timeframe on screen, so the first press is a
        // cache hit too — that is the press that used to hurt.
        const i = state.rows.findIndex((r) => r.symbol === symbol);
        if (i >= 0) {
            [state.rows[i - 1], state.rows[i + 1]].filter(Boolean).forEach((neighbour) => {
                fetchCandles(neighbour.symbol, state.interval).catch(() => {});
                if (state.interval !== WEEKLY) {
                    fetchCandles(neighbour.symbol, WEEKLY).catch(() => {});
                }
            });
        }
    }

    function step(delta) {
        const i = state.rows.findIndex((r) => r.symbol === state.symbol);
        const next = state.rows[i + delta];
        if (next) open(next.symbol);
    }

    function close() {
        state.symbol = null;
        $('cmBack').hidden = true;
        disposePanes();
    }

    const isOpen = () => !!$('cmBack') && !$('cmBack').hidden;

    function bind() {
        if (state.bound || !$('cmBack')) return;
        state.bound = true;

        $('cmClose').addEventListener('click', close);
        $('cmPrev').addEventListener('click', () => step(-1));
        $('cmNext').addEventListener('click', () => step(1));
        $('cmBack').addEventListener('click', (e) => {
            if (e.target === $('cmBack')) close();
        });
        $('cmCpr').addEventListener('click', () => {
            state.cprOn = !state.cprOn;
            syncNav();
            // The payload is already in hand — this is a redraw, not a fetch.
            if (state.symbol) loadCandles(state.symbol);
        });
        $('cmInterval').addEventListener('change', () => {
            state.interval = $('cmInterval').value;
            if (state.symbol) loadCandles(state.symbol);
        });
        // The chart takes its palette at creation, so a theme switch redraws.
        global.addEventListener('themechanged', () => {
            if (state.symbol) loadCandles(state.symbol);
        });
        // Lightweight Charts sizes to its container; the popup is a viewport
        // percentage, so a window resize has to be passed on.
        global.addEventListener('resize', () => {
            for (const pane of state.panes) fitWithRightPad(pane.chart, pane.bars);
        });
        // Capture phase, so the popup takes Escape and the arrows before the
        // page behind it moves its own selection.
        document.addEventListener('keydown', (e) => {
            if (!isOpen()) return;
            if (e.key === 'Escape') { close(); e.stopPropagation(); }
            if (e.key === 'ArrowLeft') { step(-1); e.stopPropagation(); }
            if (e.key === 'ArrowRight') { step(1); e.stopPropagation(); }
        }, true);

        // The popup is fixed-position; a transformed or contained ancestor
        // would make itself its containing block and centre it against the
        // scrolling page instead of the viewport.
        document.body.appendChild($('cmBack'));
    }

    global.CandleModal = { open, close, isOpen, mount: bind };
})(window);
