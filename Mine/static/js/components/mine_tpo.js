/**
 * mine_tpo.js — Time Price Opportunity (Market Profile) as a chart-agnostic
 * indicator engine, built the same way as mine_cpr.js and drawn beside it.
 *
 * `MineTPO.compute(candles, interval, settings, cache)` is pure: it turns one
 * pane's intraday candles into one profile per session — the TPO rows, the
 * POC, the value area, the initial balance and the single prints — and
 * `MineTPO.attach(pane, result)` puts them on a Lightweight Charts v5 pane
 * through ONE canvas primitive, so a profile costs the same as the CPR
 * overlay however many rows it has.
 *
 * The profile is the standard one: the session is cut into TPO periods (30
 * minutes by default), each period is a letter, and a letter is stamped on
 * every price row that period traded through. Rows are packed left to right
 * in time order, so the shape is the count of periods that visited a price —
 * NOT the periods' own x positions, which would draw a candle chart in
 * squares. The value area grows from the POC in row PAIRS, the larger side
 * first, until it covers `tpoVA` percent of the session's TPOs.
 *
 * Timestamps are the app's "fake IST epoch" (IST wall-clock stored as UTC
 * seconds, chart told `timezone: 'Etc/UTC'`), so every date split uses the
 * UTC getters on purpose — same convention as mine_cpr.js.
 *
 * Elements address the x-axis by BAR INDEX into the candle array and carry
 * that bar's TIME beside it, and the primitive resolves the time to the
 * chart's logical index, for the reason the CPR header sets out: another
 * series with points off the candle grid shifts every raw index.
 */
window.MineTPO = (function () {
    'use strict';

    /* ── settings ────────────────────────────────────────────────────────── */
    const DEFAULTS = {
        tpo: false,              // off until asked for — it is a lot of ink over the candles
        tpoSize: '30minute',     // one letter per period; raised to the pane's own timeframe when coarser
        tpoVA: 70,               // value area, percent of the session's TPOs
        tpoRowStep: 0,           // price per row; 0 = auto from the sessions' own range
        tpoRowsTarget: 50,       // rows a session should come out around when the step is auto
        tpoSessions: 10,         // sessions back, newest first
        tpoLetters: false,       // stamp the period's letter in each block when there is room
        tpoSplit: true,          // colour a block by its period's direction, like the split profile
        tpoVaLines: true,        // VAH / POC / VAL across the session
        tpoVaFill: true,         // shade the value area
        tpoLabels: true,         // VAH / POC / VAL text
        tpoIb: true,             // initial balance — the first two periods' range
        tpoSingles: true,        // mark the rows only one period reached
        tpoWidthPct: 40,         // a profile may take this much of its session's width
        tpoExtend: true,         // carry the last session's POC/VAH/VAL to the right edge
    };

    const LIGHT = {
        up: '#1b9981', down: '#f23645', flat: '#64748b',
        poc: '#e11d48', va: '#2563eb', vaFill: '#3b82f6',
        ib: '#7c3aed', single: '#f59e0b', letter: '#0f172a',
    };
    const DARK = Object.assign({}, LIGHT, {
        poc: '#fb7185', va: '#60a5fa', ib: '#a78bfa', letter: '#e2e8f0',
    });
    const COLORS = LIGHT;

    // The sizes offered as a TPO period. Seconds come from MineCPR so the two
    // engines cannot drift apart on what '30minute' means.
    const SIZE_OPTIONS = [
        ['5minute', '5 min'], ['10minute', '10 min'], ['15minute', '15 min'],
        ['30minute', '30 min'], ['60minute', '1 hour'],
    ];

    const MAX_ROWS = 400;        // a guard on the row loop, never reached by a real session
    const MIN_COL_PX = 1.2, MAX_COL_PX = 11;   // a block's width before the session's own cap

    const secondsOf = k => (window.MineCPR && MineCPR.SECONDS[k]) || 1800;
    const sessionStart = t => MineCPR.sessionStart(t);
    const dayKey = t => MineCPR.dayKey(t);

    const withAlpha = (hex, alpha) => {
        const n = parseInt(hex.slice(1), 16);
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
    };

    function isDark() {
        try {
            const t = window.AppTheme && window.AppTheme.getActiveTheme();
            return t === 'dark' || t === 'forest';
        } catch (e) { return false; }
    }

    /* ── where a profile can be drawn ────────────────────────────────────── */
    // A profile needs intraday bars, and a period that is at least one bar
    // wide. A pane coarser than the chosen size is not refused — its own
    // timeframe becomes the period, which is what the letters would collapse
    // to anyway — so the gate is only "intraday".
    function tfInfo(interval) {
        const secs = secondsOf(interval);
        return { secs, showTpo: secs < 86400 };
    }
    const periodSecs = (interval, settings) =>
        Math.max(secondsOf(interval), secondsOf((settings && settings.tpoSize) || DEFAULTS.tpoSize));

    /* ── row geometry ────────────────────────────────────────────────────── */
    // Rows are absolute — row i spans [i·step, (i+1)·step) — so every session
    // in the pane lines its blocks up on the same price grid.
    const rowOf = (price, step) => Math.floor(price / step);

    // A "nice" step: 1, 2, 2.5 or 5 times a power of ten, so NIFTY's ~250
    // point session lands on 5-point rows rather than 4.83-point ones.
    function niceStep(raw) {
        if (!(raw > 0)) return 1;
        const p = Math.pow(10, Math.floor(Math.log10(raw)));
        for (const m of [1, 2, 2.5, 5]) if (raw <= m * p) return m * p;
        return 10 * p;
    }

    /* ── one session's profile ───────────────────────────────────────────── */
    // bars: the session's candles, in order. Returns the drawable profile, or
    // null when the session is too thin to say anything.
    function buildProfile(bars, step, secs, vaPct, key) {
        if (!bars.length || !(step > 0)) return null;
        const open = sessionStart(bars[0].time);

        // Periods, anchored on 09:15 like every other bucket in the app.
        const periods = [];
        for (const b of bars) {
            const idx = Math.floor((b.time - open) / secs);
            let p = periods[idx];
            if (!p) p = periods[idx] = { idx, high: b.high, low: b.low, open: b.open, close: b.close };
            else { p.high = Math.max(p.high, b.high); p.low = Math.min(p.low, b.low); p.close = b.close; }
        }
        const live = periods.filter(Boolean);
        if (!live.length) return null;

        // rows: row index -> the periods that traded through it, in time order.
        const rows = new Map();
        let total = 0, minRow = Infinity, maxRow = -Infinity;
        live.forEach((p, n) => {
            const lo = rowOf(p.low, step), hi = rowOf(p.high, step);
            if (hi - lo > MAX_ROWS) return;              // a bad print, not a session
            const dir = p.close > p.open ? 1 : p.close < p.open ? -1 : 0;
            for (let r = lo; r <= hi; r++) {
                let list = rows.get(r);
                if (!list) rows.set(r, list = []);
                list.push({ n, dir });
                total++;
                if (r < minRow) minRow = r;
                if (r > maxRow) maxRow = r;
            }
        });
        if (!total) return null;

        // POC: the busiest row; the one nearest the session's middle wins a tie,
        // which is the convention and keeps it off a lone spike at an extreme.
        const mid = (minRow + maxRow) / 2;
        let poc = minRow, best = -1;
        for (const [r, list] of rows) {
            if (list.length > best || (list.length === best && Math.abs(r - mid) < Math.abs(poc - mid))) {
                best = list.length; poc = r;
            }
        }

        // Value area: from the POC outward in row pairs, taking the heavier
        // side each step, until `vaPct` of the session's TPOs is inside.
        const count = r => (rows.get(r) || []).length;
        const target = total * Math.max(1, Math.min(100, vaPct)) / 100;
        let inside = count(poc), up = poc + 1, dn = poc - 1;
        while (inside < target && (up <= maxRow || dn >= minRow)) {
            const above = up <= maxRow ? count(up) + count(up + 1) : -1;
            const below = dn >= minRow ? count(dn) + count(dn - 1) : -1;
            if (above < 0 && below < 0) break;
            if (above >= below) { inside += above; up += 2; } else { inside += below; dn -= 2; }
        }
        const vahRow = Math.min(maxRow, up - 1), valRow = Math.max(minRow, dn + 1);

        // Initial balance: the first two periods, the session's opening range.
        const ibPeriods = live.filter(p => p.idx < 2);
        const ib = ibPeriods.length
            ? { high: Math.max(...ibPeriods.map(p => p.high)), low: Math.min(...ibPeriods.map(p => p.low)) }
            : null;

        return {
            key, step, minRow, maxRow, poc, vahRow, valRow, ib,
            widest: best,
            rows: Array.from(rows.entries()).sort((a, b) => a[0] - b[0]),
            singles: Array.from(rows.entries()).filter(([, l]) => l.length === 1).map(([r]) => r),
            vah: (vahRow + 1) * step, val: valRow * step,
            pocTop: (poc + 1) * step, pocBottom: poc * step,
        };
    }

    /* ── compute: every session on the pane ──────────────────────────────── */
    // `cache` is the pane's own Map(sessionKey -> {sig, profile}). A finished
    // session never changes, so only today's is rebuilt on a live tick — the
    // reason four live panes can carry a profile each without the tick
    // getting longer.
    function compute(candles, interval, settings, cache) {
        const s = key => (settings && key in settings) ? settings[key] : DEFAULTS[key];
        const info = tfInfo(interval);
        if (!s('tpo') || !info.showTpo || !candles || !candles.length) return { profiles: [], step: 0 };

        const secs = periodSecs(interval, settings);
        const wanted = Math.max(1, s('tpoSessions') | 0);

        // Split into sessions, newest last, and keep only the ones asked for.
        const sessions = [];
        let cur = null;
        for (let i = 0; i < candles.length; i++) {
            const c = candles[i];
            const key = dayKey(c.time);
            if (!cur || cur.key !== key) sessions.push(cur = { key, bars: [], first: i, last: i });
            cur.bars.push(c); cur.last = i;
        }
        const used = sessions.slice(-wanted);
        if (!used.length) return { profiles: [], step: 0 };

        // One row step for every profile so the blocks share a price grid:
        // the median session range over the window, cut into tpoRowsTarget
        // rows and snapped. A fixed step from the settings wins outright.
        let step = +s('tpoRowStep') || 0;
        if (!(step > 0)) {
            const ranges = used.map(x => Math.max(...x.bars.map(b => b.high)) - Math.min(...x.bars.map(b => b.low)))
                               .filter(r => r > 0).sort((a, b) => a - b);
            if (!ranges.length) return { profiles: [], step: 0 };
            const median = ranges[Math.floor(ranges.length / 2)];
            step = niceStep(median / Math.max(5, s('tpoRowsTarget') | 0));
        }

        const vaPct = s('tpoVA');
        const sig = `${step}|${secs}|${vaPct}`;
        const store = cache instanceof Map ? cache : null;
        const profiles = [];
        for (let k = 0; k < used.length; k++) {
            const sess = used[k];
            // The last session is the developing one: always rebuilt. An
            // earlier one is keyed by its signature and its bar count, so a
            // history refresh that fills a gap does invalidate it.
            const stamp = `${sig}|${sess.bars.length}`;
            const last = k === used.length - 1;
            let hit = !last && store ? store.get(sess.key) : null;
            let profile = hit && hit.sig === stamp ? hit.profile : null;
            if (!profile) {
                profile = buildProfile(sess.bars, step, secs, vaPct, sess.key);
                if (store && !last) store.set(sess.key, { sig: stamp, profile });
            }
            if (!profile) continue;
            profiles.push(Object.assign({
                x1: sess.first, t1: sess.bars[0].time,
                x2: sess.last, t2: sess.bars[sess.bars.length - 1].time,
                bars: sess.bars.length,
                developing: last,
            }, profile));
        }
        if (store) {
            const live = new Set(used.map(x => x.key));
            for (const key of Array.from(store.keys())) if (!live.has(key)) store.delete(key);
        }
        return { profiles, step, secs, settings: Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, s(k)])) };
    }

    /* ── the primitive: every profile in one draw() ──────────────────────── */
    function makePrimitive() {
        const state = { result: { profiles: [] }, series: null, chart: null, requestUpdate: null };

        const renderer = {
            draw(target) {
                const { series, chart, result } = state;
                if (!series || !chart || !result.profiles.length) return;
                const crisp = window.TradingViewChart && window.TradingViewChart.crisp;
                if (!crisp) return;
                const set = result.settings || DEFAULTS;
                const C = isDark() ? DARK : LIGHT;
                const ts = chart.timeScale();

                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width;
                    const barSpacing = ts.options().barSpacing || 6;
                    const half = Math.max(1, barSpacing / 2);
                    const byTime = typeof ts.timeToIndex === 'function';

                    // Bar index -> logical index by time, as mine_cpr.js does.
                    const logicalOf = (x, t) => {
                        if (byTime && t != null) { const i = ts.timeToIndex(t, true); if (i != null) return i; }
                        return x;
                    };
                    const xOf = (x, t) => {
                        const c = ts.logicalToCoordinate(logicalOf(x, t));
                        return c === null ? null : c * hr;
                    };
                    const yOf = p => {
                        const c = series.priceToCoordinate(p);
                        return c === null ? null : c * vr;
                    };

                    ctx.save();
                    ctx.textBaseline = 'middle';

                    for (const p of result.profiles) {
                        const left = xOf(p.x1, p.t1);
                        const rightEnd = xOf(p.x2, p.t2);
                        if (left === null && rightEnd === null) continue;
                        const l = (left === null ? -half * hr : left - half * hr);
                        const r = (rightEnd === null ? W : rightEnd + half * hr);
                        if (r < 0 || l > W) continue;          // whole session off screen

                        // Block width: a share of a bar, never wider than the
                        // profile's slice of its own session — a busy day must
                        // not paint over the next one.
                        const spanPx = Math.max(1, r - l);
                        const cap = spanPx * Math.max(5, Math.min(100, set.tpoWidthPct)) / 100;
                        let colW = Math.min(MAX_COL_PX * hr, Math.max(MIN_COL_PX * hr, barSpacing * 0.62 * hr));
                        if (p.widest * colW > cap) colW = Math.max(1, cap / p.widest);

                        // One row's height in device pixels, measured across the
                        // whole profile so rounding cannot accumulate into a gap.
                        const yTop = yOf((p.maxRow + 1) * p.step), yBot = yOf(p.minRow * p.step);
                        if (yTop === null || yBot === null) continue;
                        const h = Math.max(1, Math.abs(yBot - yTop) / (p.maxRow - p.minRow + 1));

                        // Value-area shade first, so every block sits on top of it.
                        if (set.tpoVaFill) {
                            const y1 = yOf(p.vah), y2 = yOf(p.val);
                            if (y1 !== null && y2 !== null) {
                                ctx.fillStyle = withAlpha(C.vaFill, 0.07);
                                ctx.fillRect(Math.round(l), Math.round(Math.min(y1, y2)),
                                             Math.round(r - l), Math.max(1, Math.round(Math.abs(y2 - y1))));
                            }
                        }

                        // The blocks. Row by row, packed left to right in the
                        // order the periods arrived.
                        const letters = set.tpoLetters && h >= 7 * vr && colW >= 6 * hr;
                        if (letters) ctx.font = `${Math.round(Math.min(h - 2 * vr, colW))}px Inter, system-ui, sans-serif`;
                        ctx.textAlign = 'center';
                        for (const [row, list] of p.rows) {
                            const yr = yOf((row + 1) * p.step);
                            if (yr === null) continue;
                            const top = Math.round(yr);
                            if (top + h < 0 || top > scope.bitmapSize.height) continue;
                            const bh = Math.max(1, Math.round(h) - (h > 3 * vr ? Math.round(vr) : 0));
                            for (let j = 0; j < list.length; j++) {
                                const x = l + j * colW;
                                if (x > W) break;
                                const dir = list[j].dir;
                                const base = !set.tpoSplit ? C.flat : dir > 0 ? C.up : dir < 0 ? C.down : C.flat;
                                ctx.fillStyle = withAlpha(base, 0.55);
                                ctx.fillRect(Math.round(x), top, Math.max(1, Math.round(colW) - (colW > 3 * hr ? Math.round(hr) : 0)), bh);
                                if (letters) {
                                    ctx.fillStyle = withAlpha(C.letter, 0.8);
                                    ctx.fillText(letterOf(list[j].n), Math.round(x + colW / 2), top + bh / 2);
                                }
                            }
                        }

                        // Single prints: the rows one period alone reached —
                        // the gaps a move left behind, and the levels it tends
                        // to come back and fill.
                        if (set.tpoSingles) {
                            ctx.fillStyle = withAlpha(C.single, 0.85);
                            for (const row of p.singles) {
                                const yr = yOf((row + 1) * p.step);
                                if (yr === null) continue;
                                ctx.fillRect(Math.round(l - 3 * hr), Math.round(yr), Math.max(1, Math.round(2 * hr)), Math.max(1, Math.round(h)));
                            }
                        }

                        // Initial balance: a bracket at the session's left edge.
                        if (set.tpoIb && p.ib) {
                            const y1 = yOf(p.ib.high), y2 = yOf(p.ib.low);
                            if (y1 !== null && y2 !== null) {
                                const x = Math.round(l - 6 * hr), w = crisp.width(1, hr);
                                ctx.fillStyle = withAlpha(C.ib, 0.9);
                                ctx.fillRect(x, Math.round(Math.min(y1, y2)), w, Math.max(1, Math.round(Math.abs(y2 - y1))));
                                ctx.fillRect(x, Math.round(Math.min(y1, y2)), Math.round(4 * hr), w);
                                ctx.fillRect(x, Math.round(Math.max(y1, y2)) - w, Math.round(4 * hr), w);
                                if (set.tpoLabels && Math.abs(y2 - y1) > 14 * vr) {
                                    ctx.font = `${Math.round(9 * vr)}px Inter, system-ui, sans-serif`;
                                    ctx.textAlign = 'left';
                                    ctx.fillText('IB', x + 6 * hr, Math.min(y1, y2) + 6 * vr);
                                }
                            }
                        }

                        // POC / VAH / VAL. The developing session's run on to
                        // the right edge when asked, the way a live profile's
                        // levels are read forward.
                        if (set.tpoVaLines) {
                            const runOn = set.tpoExtend && p.developing;
                            const lineRight = runOn ? W : r;
                            ctx.font = `${Math.round(9 * vr)}px Inter, system-ui, sans-serif`;
                            ctx.textAlign = 'right';
                            const level = (price, color, label, width) => {
                                const y = yOf(price);
                                if (y === null) return;
                                const w = crisp.width(width, vr);
                                crisp.hline(ctx, Math.round(Math.max(l, 0)), Math.round(lineRight), Math.round(y), w, color);
                                if (set.tpoLabels) {
                                    ctx.fillStyle = color;
                                    ctx.fillText(label, Math.round(Math.min(lineRight, W) - 3 * hr), Math.round(y) - 6 * vr);
                                }
                            };
                            level(p.vah, withAlpha(C.va, 0.85), 'VAH', 1);
                            level(p.val, withAlpha(C.va, 0.85), 'VAL', 1);
                            level((p.pocTop + p.pocBottom) / 2, withAlpha(C.poc, 0.95), 'POC', 1.5);
                        }
                    }
                    ctx.restore();
                });
            },
        };

        const paneView = { renderer: () => renderer, zOrder: () => 'bottom', update() {} };
        return {
            attached(p) { state.series = p.series; state.chart = p.chart; state.requestUpdate = p.requestUpdate; },
            detached() { state.series = null; state.chart = null; state.requestUpdate = null; },
            updateAllViews() {},
            paneViews: () => [paneView],
            // Like the CPR primitive, a profile never moves the price scale:
            // the candles decide the range and the blocks fit inside it.
            autoscaleInfo: () => null,
            setResult(result) { state.result = result || { profiles: [] }; if (state.requestUpdate) state.requestUpdate(); },
        };
    }

    // A-Z, then a-z, then round again — the letters a market profile uses.
    function letterOf(n) {
        const i = n % 52;
        return String.fromCharCode(i < 26 ? 65 + i : 97 + (i - 26));
    }

    /* ── attach / detach ─────────────────────────────────────────────────── */
    // pane = { chart, series, tpoPrimitive }
    function attach(pane, result) {
        if (!pane.tpoPrimitive) {
            pane.tpoPrimitive = makePrimitive();
            pane.series.attachPrimitive(pane.tpoPrimitive);
        }
        pane.tpoPrimitive.setResult(result);
    }

    function detach(pane) {
        if (pane.tpoPrimitive) {
            try { pane.series.detachPrimitive(pane.tpoPrimitive); } catch (e) {}
            pane.tpoPrimitive = null;
        }
    }

    /* ── settings popup section ──────────────────────────────────────────── */
    // Rendered by MineCPR.renderSettings, so the rows read and behave exactly
    // like the CPR ones and a page only has to concat this in.
    const SPEC = [
        { title: 'TPO / Market Profile', gate: 'showTpo', gateLabel: 'intraday', items: [
            { key: 'tpo', label: 'TPO profile', color: COLORS.up },
            { key: 'tpoSize', type: 'select', label: 'TPO size', options: SIZE_OPTIONS, sub: true },
            { key: 'tpoSessions', type: 'number', label: 'Sessions back', min: 1, max: 60, sub: true },
            { key: 'tpoVA', type: 'number', label: 'Value area %', min: 5, max: 100, sub: true },
            { key: 'tpoRowStep', type: 'number', label: 'Row size (0 = auto)', min: 0, max: 1000, sub: true },
            { key: 'tpoWidthPct', type: 'number', label: 'Max width % of session', min: 5, max: 100, sub: true },
            { key: 'tpoVaLines', label: 'POC / VAH / VAL', color: COLORS.poc, sub: true },
            { key: 'tpoVaFill', label: 'Shade the value area', color: COLORS.vaFill, sub: true },
            { key: 'tpoExtend', label: 'Extend today’s levels right', sub: true },
            { key: 'tpoIb', label: 'Initial balance (first 2 periods)', color: COLORS.ib, sub: true },
            { key: 'tpoSingles', label: 'Single prints', color: COLORS.single, sub: true },
            { key: 'tpoSplit', label: 'Colour blocks by period direction', sub: true },
            { key: 'tpoLetters', label: 'Letters inside the blocks', sub: true },
            { key: 'tpoLabels', label: 'Level labels', sub: true },
        ] },
    ];

    return { DEFAULTS, COLORS, SPEC, SIZE_OPTIONS, tfInfo, periodSecs, compute, attach, detach, letterOf, niceStep };
})();
