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

    // TPO mode's own source. The profile is built from periods anchored on
    // 09:15, and only the broker feed is on that grid — /api/watchlist's
    // Yahoo bars start the day at 09:00, which shifts every letter by half a
    // period and can move a single print. It also arrives on the app's
    // "fake IST epoch" (IST wall clock stored as UTC seconds), which is what
    // MineTPO reads, so its bars are tagged `fakeIst` and formatted with the
    // UTC getters instead of the Asia/Kolkata ones.
    const TPO_API = '/api/multichart/candles';
    const TPO_INTERVAL = '30minute';
    const TPO_LABEL = '30m';
    // A 30-minute chart reads against DAILY pivots — the same step up
    // INTERVALS['30m'] makes for every other timeframe here.
    const TPO_CPR_PERIOD = 'Daily';
    // Camarilla's R3/S3 multiplier, as _CAMARILLA_R3 in watchlist_service.py.
    const CAMARILLA_R3 = 1.1 / 4;
    const SESSION_OPEN_SECS = 9 * 3600 + 15 * 60;

    const state = {
        symbol: null,
        rows: [],
        interval: '1d',
        // The CPR period is derived from the timeframe, so this is only
        // whether the overlay is drawn at all.
        cprOn: true,
        // TPO profile on the top pane. Off unless asked for — it is a lot of
        // ink, and it forces the pane onto 30-minute broker bars.
        tpoOn: false,
        // What a caller pinned for this open: `rowStep` is the row grid the
        // scanner measured its bands on, so the band in the grid is the band
        // on the chart. Per row, because each session sizes its own rows.
        tpoPin: {},
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

    // TPO mode's top pane. Same cache and de-dupe as fetchCandles, under a
    // key of its own so the two sources cannot be served for each other.
    //
    // Returned in this popup's payload shape, with `fakeIst` set: these
    // times are IST wall clock stored as UTC seconds, so they must be read
    // with the UTC getters. MineTPO wants them exactly that way — it is what
    // anchors a period on 09:15 — and the axis formatters below switch to
    // match rather than shifting the bars, which would move them off the
    // grid the profile's own x positions are resolved against.
    // CPR (TC/P/BC) and Camarilla R3/S3 per session, from the PREVIOUS
    // session's OHLC — the rule that makes them levels to trade against
    // rather than a restatement of the day they sit on. Same arithmetic as
    // _period_levels() server-side; only the source and the time grid
    // differ, because these have to land on the fake-IST bars beside them.
    function dailyCprLevels(daily) {
        const out = [];
        for (let i = 1; i < (daily || []).length; i++) {
            const prev = daily[i - 1];
            const day = daily[i];
            if (prev.h == null || prev.l == null || prev.c == null || !day.date) continue;
            const pp = (prev.h + prev.l + prev.c) / 3;
            const bc = (prev.h + prev.l) / 2;
            const tc = 2 * pp - bc;
            const span = prev.h - prev.l;
            const [y, m, d] = day.date.split('-').map(Number);
            out.push({
                // The session these levels are in force for, on the pane's
                // own grid: 09:15 of that day as a fake-IST epoch.
                from: Date.UTC(y, m - 1, d) / 1000 + SESSION_OPEN_SECS,
                p:  +pp.toFixed(2),
                bc: +Math.min(bc, tc).toFixed(2),
                tc: +Math.max(bc, tc).toFixed(2),
                r3: +(prev.c + span * CAMARILLA_R3).toFixed(2),
                s3: +(prev.c - span * CAMARILLA_R3).toFixed(2),
            });
        }
        return out;
    }

    function fetchTpoCandles(symbol) {
        const key = `${symbol}|tpo`;
        const hit = state.candles.get(key);
        if (hit) return Promise.resolve(hit);
        const pending = state.inflight.get(key);
        if (pending) return pending;

        const request = getJSON(
            `${TPO_API}?symbol=${encodeURIComponent(symbol)}`
            + `&interval=${TPO_INTERVAL}&source=spot`)
            .then((data) => {
                const bars = (data && data.candles) || [];
                if (!data || !data.success || !bars.length) {
                    return { success: false,
                             error: (data && data.error) || 'No 30-minute bars for this symbol' };
                }
                const payload = {
                    success: true, intraday: true, fakeIst: true,
                    cpr_period: TPO_CPR_PERIOD,
                    levels: dailyCprLevels(data.daily),
                    points: bars.map((c) => ({ t: c.time, o: c.open, h: c.high,
                                               l: c.low, c: c.close, v: c.volume })),
                };
                if (state.candles.size >= CACHE_MAX) {
                    state.candles.delete(state.candles.keys().next().value);
                }
                state.candles.set(key, payload);
                return payload;
            })
            .catch((e) => ({ success: false, error: e.message }))
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
    const IST_STAMP = istFmt({ weekday: 'short', day: '2-digit', month: 'short', year: '2-digit',
                              hour: '2-digit', minute: '2-digit', hour12: false });

    // The fake-IST grid reads as IST already, so its labels come off the UTC
    // getters — running them through Asia/Kolkata would add the offset a
    // second time and put the 09:15 bar at 14:45.
    const UTC = 'UTC';
    const utcFmt = (opts) => new Intl.DateTimeFormat('en-GB', { timeZone: UTC, ...opts });
    const UTC_TIME  = utcFmt({ hour: '2-digit', minute: '2-digit', hour12: false });
    const UTC_DAY   = utcFmt({ day: 'numeric' });
    const UTC_MONTH = utcFmt({ month: 'short' });
    const UTC_YEAR  = utcFmt({ year: 'numeric' });
    const UTC_STAMP = utcFmt({ weekday: 'short', day: '2-digit', month: 'short', year: '2-digit',
                               hour: '2-digit', minute: '2-digit', hour12: false });

    // Axis tick marks. The library picks the granularity and hands it over
    // as a TickMarkType; only the rendering of it changes here.
    function tickMarkWith(F) {
        return function (time, tickMarkType) {
            const at = toDate(time);
            if (!at) return '';
            const T = (global.LightweightCharts && global.LightweightCharts.TickMarkType)
                || { Year: 0, Month: 1, DayOfMonth: 2 };
            if (tickMarkType === T.Year) return F.year.format(at);
            if (tickMarkType === T.Month) return F.month.format(at);
            if (tickMarkType === T.DayOfMonth) return F.day.format(at);
            return F.time.format(at);
        };
    }

    // The crosshair's time label, matching window.lwCrosshairTime elsewhere in
    // the app: "Wed 02 Sep '26  13:15". This page does not load
    // tradingview-chart.js, so the format is rebuilt here off the IST formatter.
    function stampWith(F) {
        return function (time) {
            const at = toDate(time);
            if (!at) return '';
            const part = {};
            for (const piece of F.stamp.formatToParts(at)) part[piece.type] = piece.value;
            return `${part.weekday} ${part.day} ${part.month} '${part.year}  ${part.hour}:${part.minute}`;
        };
    }

    // Real epoch seconds (every Yahoo-backed timeframe) vs the fake-IST grid
    // TPO mode runs on.
    const CLOCKS = {
        ist:  { year: IST_YEAR, month: IST_MONTH, day: IST_DAY, time: IST_TIME, stamp: IST_STAMP },
        fake: { year: UTC_YEAR, month: UTC_MONTH, day: UTC_DAY, time: UTC_TIME, stamp: UTC_STAMP },
    };
    const clockFor = (payload) => (payload && payload.fakeIst) ? CLOCKS.fake : CLOCKS.ist;

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
            // and that time has to read in IST — see tickMarkWith / clockFor.
            timeScale: { borderVisible: false, timeVisible: !!payload.intraday,
                         secondsVisible: false,
                         tickMarkFormatter: payload.intraday
                             ? tickMarkWith(clockFor(payload)) : undefined },
            localization: payload.intraday
                ? { locale: 'en-IN', timeFormatter: stampWith(clockFor(payload)) }
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
        const pane = { chart, series, bars: candles.length };
        if (payload.tpo) drawTpoProfile(pane, candles);
        fitWithRightPad(chart, candles.length);
        return {
            chart,
            series,
            bars: candles.length,
            tpoPrimitive: pane.tpoPrimitive,
            // Kept for the crosshair link: the two panes are on different
            // timeframes, so a hovered bar has to be mapped onto whichever
            // bar of the other pane contains the same moment.
            times: candles.map((c) => ({ time: c.time, at: toDate(c.time), close: c.close })),
        };
    }

    // ── TPO profile ──────────────────────────────────────────────────
    //
    // MineTPO is the Multichart page's engine, unchanged: this only decides
    // how many sessions to draw and on what row grid, then hands it the
    // pane's own candles so the blocks and the candles cannot disagree.
    //
    // `tpoRowStep` is the important one. Left auto, the engine sizes rows
    // from the MEDIAN range of the sessions on screen; the scanner sized
    // them from one session's range. Two different grids put the same gap
    // at two different prices, so a caller that measured a band passes the
    // step it measured on and the chart reproduces that band exactly.
    //
    // EVERY session in the payload gets a profile — about two months of
    // them on the 30-minute feed — not a window ending at the one that
    // matched. Scrolling back has to show the profile for the day being
    // scrolled to, or the past is just candles. It is affordable because a
    // finished session is built once and cached and the draw loop skips
    // profiles that are off screen.
    function tpoSessionsBack(candles) {
        const keys = new Set();
        for (const c of candles) keys.add(new Date(c.time * 1000).toISOString().slice(0, 10));
        return Math.max(1, keys.size);
    }

    function drawTpoProfile(pane, candles) {
        if (!global.MineTPO || !global.MineCPR) return;
        const pin = state.tpoPin || {};
        const settings = {
            tpo: true,
            tpoSize: TPO_INTERVAL,
            tpoSessions: tpoSessionsBack(candles),
            tpoRowStep: pin.rowStep || 0,
        };
        try {
            const result = global.MineTPO.compute(candles, TPO_INTERVAL, settings, null);
            if (result && result.profiles && result.profiles.length) {
                global.MineTPO.attach(pane, result);
            }
        } catch (e) {
            console.error('TPO profile failed:', e);
        }
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
        const tpoBtn = $('cmTpo');
        if (tpoBtn) tpoBtn.classList.toggle('active', state.tpoOn);
        // TPO pins the top pane to 30-minute broker bars, so the timeframe
        // picker has nothing to pick while it is on — disabled rather than
        // hidden, so it is clear the setting is still there.
        $('cmInterval').disabled = state.tpoOn;
    }

    async function loadCandles(symbol) {
        const interval = state.interval;
        const tpo = state.tpoOn;
        $('cmBack').classList.add('cm-loading');

        // Both panes in flight together — they are two independent requests
        // and waiting for them in turn doubles the wait for no reason.
        let top, weekly;
        try {
            [top, weekly] = await Promise.all([
                tpo ? fetchTpoCandles(symbol) : fetchCandles(symbol, interval),
                // Not fetched in TPO mode: that pane is not drawn.
                tpo ? Promise.resolve(null) : fetchCandles(symbol, WEEKLY),
            ]);
        } catch (e) {
            top = { success: false, error: e.message };
            weekly = top;
        }
        // Stepped away, or switched timeframe or mode, while this was loading.
        if (state.symbol !== symbol || state.interval !== interval
            || state.tpoOn !== tpo) return;
        // The profile is drawn by drawPane, off the payload it is handed.
        if (top && top.success) top = Object.assign({}, top, { tpo });
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

        // TPO mode is one chart: the profile and the levels are read
        // against each other, and a half-height pane is not enough for a
        // profile to be legible in. The weekly pane is not drawn at all
        // rather than hidden — an undrawn chart is one less canvas and one
        // less resize observer.
        $('cmPanes').classList.toggle('cm-panes--single', tpo);
        state.panes = [
            paint('cmBodyTop', 'cmCapTop', top,
                  tpo ? `${TPO_LABEL} · TPO` : (INTERVAL_LABELS[interval] || interval)),
            tpo ? null : paint('cmBodyWeekly', 'cmCapBottom', weekly, INTERVAL_LABELS[WEEKLY]),
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
        if ('tpo' in options) state.tpoOn = !!options.tpo;
        // Re-read per open: stepping with ‹ › lands on another symbol's row,
        // whose band was measured on its own grid.
        state.tpoPin = options.tpo ? (options.tpoPin || {}) : {};
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
                if (state.tpoOn) {
                    fetchTpoCandles(neighbour.symbol).catch(() => {});
                    return;                 // no weekly pane to prime
                }
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
        if (!next) return;
        // In TPO mode each row carries the grid its own band was measured
        // on, so stepping has to take the neighbour's, not keep this one's.
        open(next.symbol, state.tpoOn
            ? { tpo: true, tpoPin: next.tpoPin || {} } : undefined);
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
        if ($('cmTpo')) {
            $('cmTpo').addEventListener('click', () => {
                state.tpoOn = !state.tpoOn;
                // Turned on by hand rather than from a row: no measured grid
                // to honour, so the engine sizes its own.
                if (!state.tpoOn) state.tpoPin = {};
                syncNav();
                if (state.symbol) loadCandles(state.symbol);
            });
        }
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
