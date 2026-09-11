/**
 * mine_cpr.js — the "Mine CPR" Pine script as a chart-agnostic indicator engine.
 *
 * `MineCPR.compute(candles, interval, daily, settings)` is pure: it turns one
 * pane's candles into per-bar lines (EMA ×5, VWAP ×3) and a flat list of
 * time-spanned drawing elements (CPR shelves and their shadow, R/S levels,
 * PDH↔R1 / PDL↔S1 boxes, Future CPR, Multi-CPR shelves, 2nd-candle and Monday
 * boxes). `MineCPR.attach(pane, result)` puts them on a Lightweight Charts v5
 * pane: lines as LineSeries, everything else through ONE canvas primitive,
 * which is what keeps four live panes cheap — the OI Profile page draws each
 * pivot level as its own series and cannot afford to run four times.
 *
 * Timestamps are the app's "fake IST epoch" (IST wall-clock stored as UTC
 * seconds, chart told `timezone: 'Etc/UTC'`), so every date split below uses
 * the UTC getters on purpose.
 *
 * Elements address the x-axis by BAR INDEX, not time: the primitive maps them
 * with `timeScale.logicalToCoordinate`, which also works past the last bar,
 * so a running period, a virgin band and the Future CPR can all draw into the
 * right-hand whitespace. A `series.update()` appends a bar without shifting
 * earlier indices, and every live tick recomputes against the current array
 * anyway.
 */
window.MineCPR = (function () {
    'use strict';

    /* ── settings: one key per Pine input, same defaults ─────────────────── */
    const DEFAULTS = {
        cpr: true, shadow: true, cprTransp: 80, rsTransp: 98, mcprTransp: 80,
        kind: 'camarilla',                    // traditional | fibonacci | camarilla
        pivotTf: 'auto',                      // auto | day | week | month
        pivotsBack: 15,
        dailyBased: true,
        r1: false, r2: false, r3: false, r4: false,
        s1: false, s2: false, s3: false, s4: false,
        camR3S3: true,
        pdhR1Box: true, pdlS1Box: true, histPdhl: true,     // PDH/PDL on — the one default that departs from the script
        virgin: true, virginExtend: true, virginTransp: 65,
        futureCpr: false,
        labels: false,
        emaAll: false, ema9: true, ema20: true, ema50: true, ema100: false, ema200: true,
        multiCpr: false, mcpr15: false, mcpr30: false, mcpr60: true,
        box5m: false, box1m: false,
        mondayBox: true, mondayWeeksBack: 100,
        vwapCur: true, vwapPrev: true, vwapAvg3: true, vwapLabels: false,
    };

    const LIGHT_COLORS = {
        cpr: '#00008B', cprFill: '#3366ff', virginFill: '#2952CC',   // virgin: a deeper shade of the CPR blue
        r: '#006400', s: '#ff0000', rFill: '#00cc66', sFill: '#ff0000',
        cam: '#A020F0', pdhl: '#ef07f9',
        pdhBox: '#00cc66', pdlBox: '#ff0000',
        ema9: '#22c55e', ema20: '#f97316', ema50: '#ef4444', ema100: '#3b82f6', ema200: '#111827',
        ema200Dark: '#d1d5db',
        mcpr15: '#f97316', mcpr30: '#0d9488', mcpr60: '#9333ea',
        box5m: '#00D2FF', box1m: '#FF6B6B', monday: '#000000', mondayDark: '#facc15',
        vwapCur: '#2563eb', vwapPrev: '#f97316', vwapAvg3: '#ef4444',
    };
    // The script's navy / dark-green / black are drawn for a white chart;
    // on a dark one they vanish, so those few keys get lighter twins.
    const DARK_COLORS = Object.assign({}, LIGHT_COLORS, {
        cpr: '#93c5fd', r: '#4ade80', ema200: LIGHT_COLORS.ema200Dark, monday: LIGHT_COLORS.mondayDark,
    });
    const COLORS = LIGHT_COLORS;   // exported palette (the popup's swatches)

    const SECONDS = {
        '30second': 30, 'minute': 60, '2minute': 120, '3minute': 180, '5minute': 300,
        '10minute': 600, '15minute': 900, '30minute': 1800, '60minute': 3600,
        'day': 86400, 'week': 604800, 'month': 2592000,
    };
    const SESSION_OPEN = 9 * 3600 + 15 * 60;    // 09:15 as seconds past midnight
    const SESSION_CLOSE = 15 * 3600 + 30 * 60;

    const withAlpha = (hex, transp) => {
        const a = Math.max(0, Math.min(1, (100 - transp) / 100));
        const n = parseInt(hex.slice(1), 16);
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    };

    /* ── timeframe facts (Pine's timeframe.* namespace) ──────────────────── */
    function tfInfo(interval) {
        const secs = SECONDS[interval] || 60;
        const intraday = secs < 86400;
        const mult = intraday ? secs / 60 : 0;
        return {
            secs, intraday, mult,
            isDaily: interval === 'day',
            // get_pivot_resolution(): ≤15m → D, other intraday → W, daily → M
            autoPivot: intraday ? (mult <= 15 ? 'day' : 'week') : (interval === 'day' ? 'month' : 'year'),
            showMCPR: intraday && mult <= 15,
            show5mBox: intraday && mult <= 5,
            show1mBox: intraday && mult <= 1,
            showMonday: intraday && mult <= 60,
            showVwap: intraday && mult <= 15,
        };
    }

    /* ── calendar helpers on the fake-IST grid ───────────────────────────── */
    const pad2 = n => String(n).padStart(2, '0');
    function dayKey(t) {
        const d = new Date(t * 1000);
        return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
    }
    // Monday-anchored week key (that Monday's date) / calendar month / year.
    function periodKey(t, anchor) {
        const d = new Date(t * 1000);
        if (anchor === 'year') return String(d.getUTCFullYear());
        if (anchor === 'month') return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}`;
        if (anchor === 'week') {
            const m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
            m.setUTCDate(m.getUTCDate() - ((m.getUTCDay() + 6) % 7));
            return m.toISOString().slice(0, 10);
        }
        return dayKey(t);
    }
    const dayStart = t => t - (t % 86400);                 // midnight of that day
    const sessionStart = t => dayStart(t) + SESSION_OPEN;  // 09:15 of that day
    const isMonday = t => new Date(t * 1000).getUTCDay() === 1;

    /* ── grouping ────────────────────────────────────────────────────────── */
    // Consecutive runs of candles sharing a period key, with running H/L/C.
    function groupBy(candles, keyOf) {
        const groups = [];
        let cur = null;
        candles.forEach((c, i) => {
            const key = keyOf(c.time);
            if (!cur || cur.key !== key) {
                cur = { key, startIdx: i, endIdx: i, high: c.high, low: c.low, close: c.close, open: c.open,
                        from: c.time, to: c.time };
                groups.push(cur);
            }
            cur.endIdx = i; cur.to = c.time;
            cur.high = Math.max(cur.high, c.high);
            cur.low = Math.min(cur.low, c.low);
            cur.close = c.close;
        });
        return groups;
    }

    // Intraday buckets of `secs` seconds anchored on 09:15 — the exchange's
    // own grid (a 60-min bar is 09:15–10:15, not 09:00–10:00). Returns bars.
    function aggregate(candles, secs) {
        const out = [];
        let cur = null;
        for (const c of candles) {
            const s = sessionStart(c.time);
            const start = s + Math.floor((c.time - s) / secs) * secs;
            if (!cur || cur.time !== start) {
                cur = { time: start, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 };
                out.push(cur);
            } else {
                cur.high = Math.max(cur.high, c.high);
                cur.low = Math.min(cur.low, c.low);
                cur.close = c.close;
                cur.volume += c.volume || 0;
            }
        }
        return out;
    }

    /* ── per-bar lines ───────────────────────────────────────────────────── */
    function emaAll(candles, periods) {
        const k = periods.map(p => 2 / (p + 1));
        const out = periods.map(() => []);
        if (!candles.length) return out;
        const prev = periods.map(() => candles[0].close);
        for (const c of candles) {
            for (let i = 0; i < periods.length; i++) {
                prev[i] = c.close * k[i] + prev[i] * (1 - k[i]);
                out[i].push({ time: c.time, value: prev[i] });
            }
        }
        return out;
    }

    // Session VWAP plus the previous session's closing VWAP and the average
    // of the last three closing VWAPs, each held flat across the current day.
    // An index carries no volume — every bar weighs the same in that case,
    // which is the running typical-price average charting platforms plot.
    function vwaps(candles) {
        const volumeless = !candles.some(c => (c.volume || 0) > 0);
        const cur = [], finalByDay = {}, dayOrder = [];
        let cumPV = 0, cumV = 0, day = null, last = null;
        for (const c of candles) {
            const d = dayKey(c.time);
            if (d !== day) {
                if (day !== null) finalByDay[day] = last;
                cumPV = 0; cumV = 0; last = null; day = d; dayOrder.push(d);
            }
            const v = volumeless ? 1 : (c.volume || 0);
            if (v <= 0) continue;
            cumPV += ((c.high + c.low + c.close) / 3) * v;
            cumV += v;
            last = cumPV / cumV;
            cur.push({ time: c.time, value: last });
        }
        if (day !== null) finalByDay[day] = last;

        const prevOf = {}, avg3Of = {};
        for (let i = 1; i < dayOrder.length; i++) prevOf[dayOrder[i]] = finalByDay[dayOrder[i - 1]];
        for (let i = 3; i < dayOrder.length; i++) {
            const a = finalByDay[dayOrder[i - 1]], b = finalByDay[dayOrder[i - 2]], c = finalByDay[dayOrder[i - 3]];
            if (a != null && b != null && c != null) avg3Of[dayOrder[i]] = (a + b + c) / 3;
        }
        const prev = [], avg3 = [];
        for (const c of candles) {
            const d = dayKey(c.time);
            if (prevOf[d] != null) prev.push({ time: c.time, value: prevOf[d] });
            if (avg3Of[d] != null) avg3.push({ time: c.time, value: avg3Of[d] });
        }
        return { cur, prev, avg3 };
    }

    /* ── pivot maths (Pine's arrays, one call per completed period) ──────── */
    function pivotLevels(H, L, C, kind) {
        const pp = (H + L + C) / 3, bc = (H + L) / 2, tc = (pp - bc) + pp;
        const range = H - L;
        const lv = { pp, bc, tc, pdh: H, pdl: L, range };
        if (kind === 'fibonacci') {
            lv.r1 = pp + range * 0.382; lv.r2 = pp + range * 0.618; lv.r3 = pp + range; lv.r4 = pp + range * 1.382;
            lv.s1 = pp - range * 0.382; lv.s2 = pp - range * 0.618; lv.s3 = pp - range; lv.s4 = pp - range * 1.382;
        } else {
            lv.r1 = pp * 2 - L;            lv.s1 = pp * 2 - H;
            lv.r2 = pp + range;            lv.s2 = pp - range;
            lv.r3 = pp * 2 + (H - 2 * L);  lv.s3 = pp * 2 - (2 * H - L);
            lv.r4 = pp * 3 + (H - 3 * L);  lv.s4 = pp * 3 - (3 * H - L);
        }
        lv.cr3 = C + range * 1.1 / 4;      // Camarilla R3/S3 — the script's default kind
        lv.cs3 = C - range * 1.1 / 4;
        return lv;
    }

    // Exchange daily bars folded into the pivot period, keyed like the pane's
    // candles, so "Use daily-based values" reads yesterday's true H/L/C rather
    // than whatever an intraday feed happened to cover.
    function periodOhlcFromDaily(daily, anchor) {
        const map = {};
        for (const d of daily || []) {
            const t = Date.UTC(+d.date.slice(0, 4), +d.date.slice(5, 7) - 1, +d.date.slice(8, 10)) / 1000;
            const key = periodKey(t, anchor);
            const cur = map[key];
            if (!cur) map[key] = { high: d.h, low: d.l, close: d.c };
            else { cur.high = Math.max(cur.high, d.h); cur.low = Math.min(cur.low, d.l); cur.close = d.c; }
        }
        return map;
    }

    /* ── compute ─────────────────────────────────────────────────────────── */
    function compute(candles, interval, daily, userSettings) {
        const S = Object.assign({}, DEFAULTS, userSettings || {});
        const COLORS = isDarkTheme() ? DARK_COLORS : LIGHT_COLORS;
        const tf = tfInfo(interval);
        const els = [];             // drawing elements for the primitive
        const lines = {};           // key -> {points, color, width, title}
        const n = candles.length;
        if (!n) return { elements: els, lines, tf, anchor: null };
        const lastIdx = n - 1;

        // ── EMAs (labelled SMA in the script, computed with ta.ema)
        if (S.emaAll) {
            const on = [['ema9', 9], ['ema20', 20], ['ema50', 50], ['ema100', 100], ['ema200', 200]].filter(([k]) => S[k]);
            const series = emaAll(candles, on.map(([, p]) => p));
            on.forEach(([k], i) => { lines[k] = { points: series[i], color: COLORS[k], width: 1, key: k }; });
        }

        // ── VWAP (≤15m)
        if (tf.showVwap && (S.vwapCur || S.vwapPrev || S.vwapAvg3)) {
            const v = vwaps(candles);
            if (S.vwapCur)  lines.vwapCur  = { points: v.cur,  color: COLORS.vwapCur,  width: 2, title: S.vwapLabels ? 'VWAP' : '' };
            if (S.vwapPrev) lines.vwapPrev = { points: v.prev, color: COLORS.vwapPrev, width: 2, title: S.vwapLabels ? 'Prev VWAP' : '' };
            if (S.vwapAvg3) lines.vwapAvg3 = { points: v.avg3, color: COLORS.vwapAvg3, width: 2, title: S.vwapLabels ? 'Avg 3 VWAP' : '' };
        }

        // ── CPR: one shelf per period, levels from the PREVIOUS period
        const anchor = S.pivotTf === 'auto' ? tf.autoPivot : S.pivotTf;
        const useDaily = S.dailyBased && daily && daily.length;
        const dailyByPeriod = useDaily ? periodOhlcFromDaily(daily, anchor) : null;
        const periods = groupBy(candles, t => periodKey(t, anchor));

        if (S.cpr && periods.length > 1) {
            const showR = ['r1', 'r2', 'r3', 'r4'].filter(k => S[k]);
            const showS = ['s1', 's2', 's3', 's4'].filter(k => S[k]);
            const rsOn = S.kind !== 'camarilla';
            const first = Math.max(1, periods.length - S.pivotsBack);
            for (let i = first; i < periods.length; i++) {
                const prev = periods[i - 1], cur = periods[i];
                const src = (dailyByPeriod && dailyByPeriod[prev.key]) || prev;
                const lv = pivotLevels(src.high, src.low, src.close, S.kind);
                const isLive = i === periods.length - 1;
                const bandLo = Math.min(lv.bc, lv.tc), bandHi = Math.max(lv.bc, lv.tc);
                const touches = (a, b) => { for (let k = a; k <= b; k++) if (candles[k].low <= bandHi && candles[k].high >= bandLo) return k; return -1; };

                // Virgin: nothing traded into the band during its own session.
                // The running session is never styled virgin yet.
                let virgin = false, x2 = cur.endIdx, extendRight = false;
                if (S.virgin && !isLive && touches(cur.startIdx, cur.endIdx) < 0) {
                    virgin = true;
                    if (S.virginExtend) {
                        let hit = -1;
                        for (let p = i + 1; p < periods.length && hit < 0; p++) {
                            if (touches(periods[p].startIdx, periods[p].endIdx) >= 0) hit = p;
                        }
                        if (hit < 0) extendRight = true;          // never touched — into the future
                        else x2 = periods[hit].endIdx;             // stop at the end of the touch day
                    }
                }
                // The running period's levels are the ones being traded against,
                // so they run to the right edge — unless Future CPR needs that room.
                if (isLive && !S.futureCpr) extendRight = true;

                const x1 = cur.startIdx;
                const lbl = S.labels;
                const cprColor = COLORS.cpr;
                // Pine's line/box objects never move the price scale — only
                // plots do. The one exception kept here: the RUNNING period's
                // band and Camarilla R3/S3, so the levels being traded against
                // stay on screen. Everything historical scrolls like any object.
                const sc = isLive;
                if (S.shadow) {
                    els.push({ kind: 'rect', x1, x2, extendRight, y1: bandHi, y2: bandLo,
                               fill: virgin ? withAlpha(COLORS.virginFill, S.virginTransp) : withAlpha(COLORS.cprFill, S.cprTransp),
                               scale: sc });
                }
                els.push({ kind: 'line', x1, x2, extendRight, y: lv.pp, color: cprColor, width: 1, label: lbl && 'P',  scale: sc });
                els.push({ kind: 'line', x1, x2, extendRight, y: lv.bc, color: cprColor, width: 1, label: lbl && 'BC', scale: sc });
                els.push({ kind: 'line', x1, x2, extendRight, y: lv.tc, color: cprColor, width: 1, label: lbl && 'TC', scale: sc });

                // R/S lines (Traditional / Fibonacci only), R1–R2 and S1–S2 shadows
                const rx2 = cur.endIdx, rExt = isLive && !S.futureCpr;
                if (rsOn) {
                    for (const k of showR) els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv[k], color: COLORS.r, width: 1, label: lbl && k.toUpperCase() });
                    for (const k of showS) els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv[k], color: COLORS.s, width: 1, label: lbl && k.toUpperCase() });
                    if (S.shadow && S.r1 && S.r2) els.push({ kind: 'rect', x1, x2: rx2, extendRight: rExt, y1: lv.r2, y2: lv.r1, fill: withAlpha(COLORS.rFill, S.rsTransp) });
                    if (S.shadow && S.s1 && S.s2) els.push({ kind: 'rect', x1, x2: rx2, extendRight: rExt, y1: lv.s1, y2: lv.s2, fill: withAlpha(COLORS.sFill, S.rsTransp) });
                }
                // PDH↔R1 / PDL↔S1 boxes — independent of the R/S flags and the kind
                if (S.pdhR1Box) els.push({ kind: 'rect', x1, x2: rx2, extendRight: rExt, y1: Math.max(lv.pdh, lv.r1), y2: Math.min(lv.pdh, lv.r1), fill: withAlpha(COLORS.pdhBox, 85) });
                if (S.pdlS1Box) els.push({ kind: 'rect', x1, x2: rx2, extendRight: rExt, y1: Math.max(lv.pdl, lv.s1), y2: Math.min(lv.pdl, lv.s1), fill: withAlpha(COLORS.pdlBox, 85) });
                // Camarilla R3/S3
                if (S.camR3S3 && S.kind === 'camarilla') {
                    els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv.cr3, color: COLORS.cam, width: 2, label: lbl && 'R3', scale: sc });
                    els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv.cs3, color: COLORS.cam, width: 2, label: lbl && 'S3', scale: sc });
                }
                if (S.histPdhl) {
                    els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv.pdh, color: withAlpha(COLORS.pdhl, 10), width: 1, label: lbl && 'PDH' });
                    els.push({ kind: 'line', x1, x2: rx2, extendRight: rExt, y: lv.pdl, color: withAlpha(COLORS.pdhl, 10), width: 1, label: lbl && 'PDL' });
                }
            }
        }

        // ── Future CPR: next period's levels from the running period, dashed,
        //    drawn in the whitespace past the last bar.
        if (S.futureCpr && periods.length) {
            const run = periods[periods.length - 1];
            const src = (dailyByPeriod && dailyByPeriod[run.key]) || run;
            // Today's exchange bar may lag the intraday feed; take the wider of the two.
            const H = Math.max(src.high, run.high), L = Math.min(src.low, run.low), C = run.close;
            const lv = pivotLevels(H, L, C, S.kind);
            const f = { kind: 'line', x1: lastIdx + 1, x2: lastIdx + 1, extendRight: true, dash: [4, 3], width: 1 };
            els.push(Object.assign({}, f, { y: lv.pp, color: COLORS.cpr }));
            els.push(Object.assign({}, f, { y: lv.bc, color: COLORS.cpr }));
            els.push(Object.assign({}, f, { y: lv.tc, color: COLORS.cpr }));
            if (S.shadow) els.push({ kind: 'rect', x1: lastIdx + 1, x2: lastIdx + 1, extendRight: true, y1: Math.max(lv.bc, lv.tc), y2: Math.min(lv.bc, lv.tc), fill: withAlpha(COLORS.cprFill, S.cprTransp) });
            if (S.kind !== 'camarilla') {
                for (const k of ['r1', 'r2', 'r3', 'r4']) if (S[k]) els.push(Object.assign({}, f, { y: lv[k], color: COLORS.r }));
                for (const k of ['s1', 's2', 's3', 's4']) if (S[k]) els.push(Object.assign({}, f, { y: lv[k], color: COLORS.s }));
            }
            if (S.pdhR1Box) els.push({ kind: 'rect', x1: lastIdx + 1, x2: lastIdx + 1, extendRight: true, y1: Math.max(lv.pdh, lv.r1), y2: Math.min(lv.pdh, lv.r1), fill: withAlpha(COLORS.pdhBox, 85) });
            if (S.pdlS1Box) els.push({ kind: 'rect', x1: lastIdx + 1, x2: lastIdx + 1, extendRight: true, y1: Math.max(lv.pdl, lv.s1), y2: Math.min(lv.pdl, lv.s1), fill: withAlpha(COLORS.pdlBox, 85) });
            if (S.camR3S3 && S.kind === 'camarilla') {
                els.push(Object.assign({}, f, { y: lv.cr3, color: COLORS.cam, width: 2 }));
                els.push(Object.assign({}, f, { y: lv.cs3, color: COLORS.cam, width: 2 }));
            }
            els.push(Object.assign({}, f, { y: lv.pdh, color: withAlpha(COLORS.pdhl, 10) }));
            els.push(Object.assign({}, f, { y: lv.pdl, color: withAlpha(COLORS.pdhl, 10) }));
        }

        // ── Multi CPR (≤15m): 15/30/60-minute blocks, each shelf from the
        //    previous block's H/L/C; shelves per block rather than per bar.
        if (S.multiCpr && tf.showMCPR) {
            for (const [key, secs, flag] of [['mcpr15', 900, S.mcpr15], ['mcpr30', 1800, S.mcpr30], ['mcpr60', 3600, S.mcpr60]]) {
                if (!flag) continue;
                const blocks = groupBy(candles, t => { const s = sessionStart(t); return `${dayKey(t)}#${Math.floor((t - s) / secs)}`; });
                for (let i = 1; i < blocks.length; i++) {
                    const p = blocks[i - 1], b = blocks[i];
                    const pp = (p.high + p.low + p.close) / 3, bc = (p.high + p.low) / 2, tc = pp - bc + pp;
                    const isLive = i === blocks.length - 1;
                    const e = { x1: b.startIdx, x2: b.endIdx, extendRight: isLive && !S.futureCpr };
                    els.push(Object.assign({ kind: 'rect', y1: Math.max(bc, tc), y2: Math.min(bc, tc), fill: withAlpha(COLORS[key], S.mcprTransp) }, e));
                    for (const y of [pp, bc, tc]) els.push(Object.assign({ kind: 'line', y, color: COLORS[key], width: 1 }, e));
                }
            }
        }

        // ── 2nd candle boxes: the bucket after the open (09:20 on 5m, 09:16 on 1m),
        //    its H/L carried to the end of the day.
        const candleBox = (secs, color) => {
            const days = groupBy(candles, dayKey);
            for (let d = Math.max(0, days.length - 30); d < days.length; d++) {
                const day = days[d];
                const s = sessionStart(day.from);
                let hi = -Infinity, lo = Infinity, firstIdx = -1;
                for (let k = day.startIdx; k <= day.endIdx; k++) {
                    const bucket = Math.floor((candles[k].time - s) / secs);
                    if (bucket < 1) continue;
                    if (bucket > 1) break;
                    if (firstIdx < 0) firstIdx = k;
                    hi = Math.max(hi, candles[k].high); lo = Math.min(lo, candles[k].low);
                }
                if (firstIdx < 0) continue;
                const isToday = d === days.length - 1;
                els.push({ kind: 'rect', x1: firstIdx, x2: day.endIdx, extendRight: isToday && !S.futureCpr, y1: hi, y2: lo,
                           fill: withAlpha(color, 90), stroke: color, strokeWidth: 1 });
            }
        };
        if (S.box5m && tf.show5mBox) candleBox(300, COLORS.box5m);
        if (S.box1m && tf.show1mBox) candleBox(60, COLORS.box1m);

        // ── Monday H/L box: Monday's range, carried to the end of that week.
        if (S.mondayBox && tf.showMonday) {
            const weeks = groupBy(candles, t => periodKey(t, 'week'));
            for (let w = Math.max(0, weeks.length - S.mondayWeeksBack); w < weeks.length; w++) {
                const wk = weeks[w];
                let hi = -Infinity, lo = Infinity, firstIdx = -1;
                for (let k = wk.startIdx; k <= wk.endIdx; k++) {
                    if (!isMonday(candles[k].time)) { if (firstIdx >= 0) break; continue; }
                    if (firstIdx < 0) firstIdx = k;
                    hi = Math.max(hi, candles[k].high); lo = Math.min(lo, candles[k].low);
                }
                if (firstIdx < 0) continue;
                const isLive = w === weeks.length - 1;
                els.push({ kind: 'rect', x1: firstIdx, x2: wk.endIdx, extendRight: isLive && !S.futureCpr, y1: hi, y2: lo,
                           fill: 'rgba(0,0,0,0)', stroke: COLORS.monday, strokeWidth: 2 });
            }
        }

        return { elements: els, lines, tf, anchor };
    }

    function isDarkTheme() {
        try {
            const t = window.AppTheme && window.AppTheme.getActiveTheme();
            return t === 'dark' || t === 'forest';
        } catch (e) { return false; }
    }

    /* ── the primitive: every element in one draw() ──────────────────────── */
    function makePrimitive() {
        const state = { elements: [], series: null, chart: null, requestUpdate: null };

        const renderer = {
            draw(target) {
                const { series, chart, elements } = state;
                if (!series || !chart || !elements.length) return;
                const ts = chart.timeScale();
                target.useBitmapCoordinateSpace(scope => {
                    const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                    const W = scope.bitmapSize.width;
                    const half = Math.max(1, (ts.options().barSpacing || 6) / 2);
                    const xOf = idx => { const c = ts.logicalToCoordinate(idx); return c === null ? null : c; };
                    const yOf = p => { if (p == null || !isFinite(p)) return null; const c = series.priceToCoordinate(p); return c === null ? null : c * vr; };
                    ctx.save();
                    ctx.font = `${Math.round(9 * vr)}px Inter, system-ui, sans-serif`;
                    ctx.textBaseline = 'bottom';
                    for (const e of elements) {
                        const cx1 = xOf(e.x1), cx2 = e.extendRight ? null : xOf(e.x2);
                        if (cx1 === null && !e.extendRight) continue;
                        // Half a bar of overhang each side so a shelf spans its
                        // whole period instead of stopping mid-candle.
                        let left = cx1 === null ? -half : cx1 - half;
                        let right = e.extendRight ? W / hr : (cx2 === null ? W / hr : cx2 + half);
                        if (right < 0 || left > W / hr) continue;
                        left = Math.max(left, -1) * hr; right = Math.min(right, W / hr + 1) * hr;
                        if (e.kind === 'rect') {
                            const y1 = yOf(e.y1), y2 = yOf(e.y2);
                            if (y1 === null || y2 === null) continue;
                            const top = Math.min(y1, y2), h = Math.abs(y2 - y1);
                            if (e.fill) { ctx.fillStyle = e.fill; ctx.fillRect(left, top, right - left, h); }
                            if (e.stroke) {
                                ctx.strokeStyle = e.stroke; ctx.lineWidth = (e.strokeWidth || 1) * vr; ctx.setLineDash([]);
                                ctx.strokeRect(left, top, right - left, h);
                            }
                        } else {
                            const y = yOf(e.y);
                            if (y === null) continue;
                            ctx.strokeStyle = e.color; ctx.lineWidth = (e.width || 1) * vr;
                            ctx.setLineDash(e.dash ? e.dash.map(d => d * hr) : []);
                            ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
                            if (e.label) {
                                ctx.fillStyle = e.color;
                                ctx.fillText(e.label, Math.max(left, 0) + 3 * hr, y - 1 * vr);
                            }
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
            // Only elements flagged `scale` (the running period's CPR band and
            // Camarilla R3/S3) join the autoscale, and only while on screen.
            //
            // An element's OWN span [x1, x2] is what counts, never its
            // right-hand extension: a virgin band from ten sessions ago that
            // runs to the edge is drawn there, but TradingView's line objects
            // never move the scale and neither should it — after a trend it
            // would pin the range to a level far from today's bars.
            autoscaleInfo(first, last) {
                let min = Infinity, max = -Infinity;
                for (const e of state.elements) {
                    if (!e.scale) continue;
                    if (first != null && e.x2 < first) continue;
                    if (last != null && e.x1 > last) continue;
                    for (const v of e.kind === 'rect' ? [e.y1, e.y2] : [e.y]) {
                        if (v == null || !isFinite(v)) continue;
                        min = Math.min(min, v); max = Math.max(max, v);
                    }
                }
                return Number.isFinite(min) ? { priceRange: { minValue: min, maxValue: max } } : null;
            },
            setElements(els) { state.elements = els || []; if (state.requestUpdate) state.requestUpdate(); },
        };
    }

    /* ── attach: put a compute() result on a pane ────────────────────────── */
    // pane = { chart, series, lines: {}, primitive }
    function attach(pane, result) {
        if (!pane.primitive) {
            pane.primitive = makePrimitive();
            pane.series.attachPrimitive(pane.primitive);
        }
        pane.lines = pane.lines || {};
        const wanted = result.lines || {};
        for (const key of Object.keys(wanted)) {
            const spec = wanted[key];
            const color = spec.color;
            let s = pane.lines[key];
            if (!s) {
                s = pane.chart.addSeries(LightweightCharts.LineSeries, {
                    color, lineWidth: spec.width || 1, priceLineVisible: false,
                    lastValueVisible: !!spec.title, title: spec.title || '',
                    crosshairMarkerVisible: false,
                });
                pane.lines[key] = s;
            } else {
                s.applyOptions({ color, lineWidth: spec.width || 1, lastValueVisible: !!spec.title, title: spec.title || '' });
            }
            s.setData(spec.points);
        }
        for (const key of Object.keys(pane.lines)) {
            if (!wanted[key]) pane.lines[key].setData([]);
        }
        pane.primitive.setElements(result.elements);
        try { if (window.lwBringToFront) window.lwBringToFront(pane.series); } catch (e) {}
    }

    function detach(pane) {
        for (const key of Object.keys(pane.lines || {})) { try { pane.chart.removeSeries(pane.lines[key]); } catch (e) {} }
        pane.lines = {};
        if (pane.primitive) { try { pane.series.detachPrimitive(pane.primitive); } catch (e) {} pane.primitive = null; }
    }

    return { DEFAULTS, COLORS, SECONDS, tfInfo, compute, attach, detach, aggregate, periodKey, dayKey, sessionStart };
})();
