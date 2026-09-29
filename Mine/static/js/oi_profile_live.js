/**
 * oi_profile_live.js — the OI Profile chart's live feed.
 *
 * The block used to be "On Refresh": its candles only moved when someone
 * pressed Refresh All, which on a Fut chart meant watching a dead tape. This
 * is the same loop /multichart runs, pointed at this one chart:
 *
 *   every 2 s (market open)  GET /api/multichart/live?symbol=…&source=…
 *   → today's 1-MINUTE bars, re-bucketed here into the block's timeframe on
 *     the exchange's own 09:15 grid, patched onto the tail with series.update()
 *   every 5 min              one index-only /oi-profile/candles re-fetch, which
 *     heals gaps the patch cannot (a bar the feed missed) and refreshes max pain
 *
 * Deliberately narrow:
 *
 *  · **This chart only.** The Opt Prem charts below are option premiums and
 *    have no spot/future of their own; the live endpoint does not serve them
 *    and nothing here touches them. The Round Strike block runs its own 1 s
 *    poll and is likewise untouched.
 *  · **It follows the Spot / Fut switch**, so the live bars are always the
 *    instrument the chart is drawing — the whole point of the switch.
 *  · **The OI ladder, the strike dropdowns and the option chain stay manual.**
 *    They come off a full option-chain read, which is not something to put on
 *    a 2-second timer; Refresh All is still what moves them.
 *
 * Bars are on the app's fake-IST grid (IST clock as UTC seconds), the same one
 * /oi-profile/candles emits, so the two sets concatenate without conversion.
 *
 * One coupling worth knowing: /api/multichart/live resolves its broker through
 * MULTICHART_DATA_PROVIDER (unset → DATA_PROVIDER) while the history comes back
 * through OI_PROFILE_DATA_PROVIDER. Both are FYERS today, so the feed and the
 * history are the same tape. Point one of them at another broker and today's
 * bars would be that broker's — the 5-minute heal re-lays them, so it shows up
 * as today's candles shifting on the heal rather than anything silent.
 */
(function () {
    'use strict';

    const POLL_MS = { open: 2000, hidden: 10000, closed: 60000, error: 5000 };
    const HEAL_MS = 5 * 60 * 1000;     // the index-only re-fetch above
    // Timeframes this loop can rebuild from 1-minute bars. 30-second bars
    // cannot be (the feed's own bars are coarser than the chart's) and
    // week/month are not intraday, so both keep the manual badge and the
    // manual behaviour rather than being patched with something wrong.
    const LIVE_TFS = new Set(['minute', '2minute', '3minute', '5minute', '10minute',
                              '15minute', '30minute', '60minute', 'day']);

    let timer = null;
    let abortCtl = null;
    let lastHeal = 0;
    let lastBarTime = 0;    // the forming bar's stamp last tick — a new one is a new bar

    const badgeEl = () => document.getElementById('oipLiveBadge');

    // Green pulsing pill = a live tape. Amber static = nothing is arriving, and
    // it says why: a chart that has quietly stopped updating is worse than one
    // that never claimed to.
    function badge(live, text) {
        const el = badgeEl();
        if (!el) return;
        el.classList.toggle('oip-title-badge--manual', !live);
        el.textContent = text;
    }

    const canLive = () => typeof oipInterval === 'string' && LIVE_TFS.has(oipInterval);

    // NSE hours in IST, holidays included (common.js); the local fallback is
    // only for a page loaded without it.
    function marketOpenNow() {
        if (typeof window.isMarketOpen === 'function') return window.isMarketOpen();
        const d = new Date((Math.floor(Date.now() / 1000) + 19800) * 1000);
        const dow = d.getUTCDay(), mins = d.getUTCHours() * 60 + d.getUTCMinutes();
        return dow >= 1 && dow <= 5 && mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
    }

    const stamp = () => new Date().toLocaleTimeString('en-IN', { hour12: false });

    function schedule(ms) {
        clearTimeout(timer);
        timer = setTimeout(tick, ms);
    }

    /* ── today's minute bars → this chart's timeframe ─────────────────────── */
    function rebucket(minuteBars) {
        if (oipInterval === 'day') {
            const t0 = minuteBars[0].time;
            return [{
                time: t0 - (t0 % 86400),
                open: minuteBars[0].open,
                high: Math.max(...minuteBars.map(b => b.high)),
                low: Math.min(...minuteBars.map(b => b.low)),
                close: minuteBars[minuteBars.length - 1].close,
                volume: minuteBars.reduce((s, b) => s + (b.volume || 0), 0),
            }];
        }
        return MineCPR.aggregate(minuteBars, MineCPR.SECONDS[oipInterval]);
    }

    const sameBar = (a, b) => a && b && a.time === b.time && a.open === b.open &&
                              a.high === b.high && a.low === b.low && a.close === b.close;

    // Replaces TODAY's bars wholesale with the feed's own aggregation and keeps
    // every earlier session as loaded. Doing it per-day rather than per-bar is
    // what keeps the two sources from disagreeing about where a bucket starts.
    function mergeLive(minuteBars) {
        if (!minuteBars.length || !oipOISeries) return false;
        const old = oipOILastCandles;
        if (!old || !old.length) return false;

        const today = rebucket(minuteBars);
        if (!today.length) return false;

        const firstT = today[0].time;
        let cut = old.length;
        while (cut > 0 && old[cut - 1].time >= firstT) cut--;
        // The chart is showing a LATER day than the feed — a replayed/date-ranged
        // window. Patching it with today's bars would be a lie; leave it alone.
        if (cut === 0 && old[0].time > firstT) return false;

        const oldToday = old.slice(cut);
        const merged = old.slice(0, cut).concat(today);

        // series.update() may only touch the last bar or append one. Anything
        // else — a finished bar revised, a gap healed — is a full setData.
        let onlyTail = today.length >= oldToday.length && today.length - oldToday.length <= 1;
        for (let i = 0; onlyTail && i < oldToday.length - 1; i++) {
            if (!sameBar(oldToday[i], today[i])) onlyTail = false;
        }
        if (onlyTail && oldToday.length && today.length > oldToday.length &&
            !sameBar(oldToday[oldToday.length - 1], today[oldToday.length - 1])) onlyTail = false;

        const last = today[today.length - 1];
        if (onlyTail && oldToday.length && sameBar(last, oldToday[oldToday.length - 1])) {
            return false;                      // nothing moved since the last tick
        }

        oipOILastCandles = merged;
        oipLatestIndexCandles = merged;        // the Opt Prem charts align to this timeline

        const fm = oip5mCloseSettings('main');
        const marked = oipMarkSynthetic(oipMark5mCloseBorders(merged, fm.enabled, fm.color));
        try {
            if (onlyTail) oipOISeries.update(marked[marked.length - 1]);
            else oipOISeries.setData(marked);
        } catch (e) { console.warn('[OIP live] paint:', e); return false; }
        oipSetSyntheticBanner(typeof oipHasSynthetic === 'function' ? oipHasSynthetic(merged) : false);

        // A forming bar barely moves the levels, so the heavy overlays are only
        // recomputed when the tape rolls into a NEW bar. Everything cheap — the
        // CPR/EMA/VWAP set, the OI canvas — runs every tick.
        const rolled = last.time !== lastBarTime;
        lastBarTime = last.time;

        oipApplyMineCpr();
        if (rolled) {
            oipDraw2ndCandle30sBox(merged);
            oipDraw2nd5mCandleBox(merged);
            oipDraw30mReversalLines(merged);
            oipDraw1DReversalLines(merged);
            oipDrawAtmCeOiLines();
        }
        oipUpdateVwapBiasCard(merged);
        oipRequestDraw();
        return true;
    }

    /* ── the volume histogram ─────────────────────────────────────────────── */
    // Today's minute volume, bucketed the same way and written over today's
    // entries in the set the last full load brought back. BANKNIFTY's overlay
    // is not in this feed and keeps whatever the last Refresh All gave it.
    function mergeVolume(minuteVol) {
        if (!minuteVol || !minuteVol.length || !oipVolumeSeries) return;
        const secs = oipInterval === 'day' ? 86400 : MineCPR.SECONDS[oipInterval];
        if (!secs) return;
        const sums = new Map();
        for (const v of minuteVol) {
            const t = Number(v.time);
            const s = MineCPR.sessionStart(t);
            const key = oipInterval === 'day' ? t - (t % 86400)
                                              : s + Math.floor((t - s) / secs) * secs;
            sums.set(key, (sums.get(key) || 0) + Number(v.volume || 0));
        }
        const kept = (oipOIData?.future_volume || []).filter(v => !sums.has(Number(v.time)));
        const out = kept.concat([...sums].map(([time, volume]) => ({ time, volume })))
                        .sort((a, b) => a.time - b.time);
        if (oipOIData) oipOIData.future_volume = out;
        oipSetVolumeBars(oipVolumeSeries, out, oipOILastCandles);
    }

    /* ── the loop ─────────────────────────────────────────────────────────── */
    async function tick() {
        if (abortCtl) abortCtl.abort();

        // A timeframe this loop cannot rebuild, or a replay/date-ranged window:
        // say so in the badge and keep checking cheaply, since the TF dropdown
        // can put it back on a live one at any moment.
        if (!canLive() || window.oipReplayMode) {
            badge(false, 'On Refresh');
            schedule(POLL_MS.closed);
            return;
        }
        if (!marketOpenNow()) {
            badge(false, `Closed · ${stamp()}`);
            schedule(POLL_MS.closed);
            return;
        }
        // Don't race a full load: Refresh All and the Spot / Fut switch are both
        // pushing their own setData into this same series.
        //
        // NOT window._oipDataRefreshing — that one suppresses the cross-chart
        // range sync and is cleared from a requestAnimationFrame, which never
        // runs while the tab is in the background. Guarding on it stopped this
        // loop dead in a hidden tab, which is exactly when it matters most.
        if (oipIsRefreshing || oipChartSrcBusy) {
            schedule(POLL_MS.open);
            return;
        }

        const ctrl = new AbortController();
        abortCtl = ctrl;
        const symbol = oipSymbol, source = oipChartSource;
        let next = POLL_MS.error;
        try {
            const res = await fetch(
                `/api/multichart/live?symbol=${encodeURIComponent(symbol)}&source=${source}`,
                { credentials: 'same-origin', signal: ctrl.signal });
            const body = await res.json();
            if (!res.ok || body.success === false) {
                // A symbol this feed does not serve, or a broker that is not
                // logged in, is not going to fix itself in five seconds — back
                // off to the slow timer instead of hammering it all session.
                const permanent = res.status === 400 || res.status === 401;
                const why = body.error || `HTTP ${res.status}`;
                badge(false, permanent ? (body.auth_required ? 'Login required' : 'No live feed')
                                       : 'Feed error');
                console.warn('[OIP live]', why);
                schedule(permanent ? POLL_MS.closed : POLL_MS.error);
                return;
            }
            // Symbol or source changed while this was in flight — its own reload
            // has already rescheduled us, and these bars are the old instrument's.
            if (symbol !== oipSymbol || source !== oipChartSource) return;

            const open = !!body.market_open;
            const bars = body.candles || [];
            mergeLive(bars);
            mergeVolume(body.future_volume || []);
            // "Live" only when bars are actually arriving — an empty feed on an
            // open market is a broker that has gone quiet, not a live chart.
            if (open && bars.length) badge(true, `Live ${stamp()}`);
            else if (open) badge(false, 'Waiting for bars');
            else badge(false, `Closed · ${stamp()}`);
            next = document.hidden ? POLL_MS.hidden : (open ? POLL_MS.open : POLL_MS.closed);

            // The periodic heal. Index-only and forced, so it is one cheap trip
            // that never touches the option legs — it re-lays the history the
            // patch above can only ever extend, and brings max pain with it.
            if (open && Date.now() - lastHeal > HEAL_MS) {
                lastHeal = Date.now();
                const wasForcing = oipForceNextFetch;
                oipForceNextFetch = true;
                try { await oipLoadCandles(true, false, true); }
                catch (e) { console.warn('[OIP live] heal:', e); }
                finally { oipForceNextFetch = wasForcing; }
            }
        } catch (e) {
            if (e.name === 'AbortError') return;
            console.warn('[OIP live]', e);
            badge(false, 'Feed error');
        }
        schedule(next);
    }

    // A backgrounded tab drops to a 10 s poll; coming back asks immediately
    // rather than waiting out the long timer.
    document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });

    document.addEventListener('DOMContentLoaded', () => {
        if (window.oipReplayMode) return;
        if (typeof MineCPR === 'undefined') return;   // no aggregator, no live loop
        // Behind the first full load: the patch needs history to attach to.
        lastHeal = Date.now();
        schedule(4000);
    });

    // The TF dropdown can move between a patchable timeframe and one that is
    // not, and a new timeframe needs a new bucket stamp before the next patch.
    document.addEventListener('change', e => {
        if (e.target && e.target.id === 'oipInterval') { lastBarTime = 0; schedule(2500); }
    });
})();
