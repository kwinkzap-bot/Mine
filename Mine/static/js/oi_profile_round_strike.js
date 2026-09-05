/**
 * Round Strike block — a second, independent option-premium chart block on the
 * OI Profile page (see oi_profile.html "Round Strike" section). Shows a single
 * Combined (CE+PE) chart only — no CE Only / PE Only panels. Unlike Opt
 * Prem's ATM-relative strike selection, this block auto-picks CE/PE strikes
 * from today's session-open price, rounded to the nearest psychologically
 * significant "round" 100 level:
 *
 *   near50 = round(open / 50) * 50
 *   if near50 is itself a multiple of 100  -> CE = near50 - 100, PE = near50 + 100
 *   else (near50 sits at the X50 half-mark) -> CE = near50 - 50,  PE = near50 + 50
 *
 * Examples: open=23907 -> near50=23900 (mult of 100) -> CE=23800, PE=24000
 *           open=23933 -> near50=23950 (half-mark)    -> CE=23900, PE=24000
 *
 * The open price and strike list are fetched by THIS block itself
 * (oipRSFetchOpenAndStrikes), independent of Opt Prem's own load/state —
 * so the default is always the day's actual open, never a live/current
 * price, and never racing Opt Prem's own fetch timing.
 *
 * Carried by TWO pages now — OI Profile and Replay (templates/oi_replay.html)
 * — which is why the helpers it reaches for (fmtL, the CPR/IVP header
 * renderers, the stop-order path) live in oi_profile_shared.js rather than
 * oi_profile.js: Replay cannot load oi_profile.js at all, because that file
 * and oi_replay.js declare the same top-level `let`s. What it still takes
 * from whichever main file the host page does load is the page-level state
 * both of them define (oipSymbol, oipCalculateVWAP) plus showNotification —
 * all plain top-level declarations in classic <script> tags loaded earlier,
 * so they're visible here too. The timeframe is NOT shared: this block has
 * its own dropdown and its own oipRSInterval (see below).
 *
 * Order buttons deliberately use their OWN class (.oip-rs-order-btn, not
 * .oip-order-btn) and their own state (oipRSCurrentCEStrike/PEStrike, own
 * order-mode/limit-price inputs) — oi_profile.js wires every .oip-order-btn
 * on the page to Opt Prem's globals via a single page-wide querySelectorAll,
 * so sharing that class would fire orders against the wrong strike/price.
 *
 * Also draws a set of level indicators, every one of them a STEP series that
 * re-bases each session — so a day shows its own numbers and the history stays
 * on the chart, rather than one flat line restating today's value everywhere:
 *
 *   - Five per leg, CE in green and PE in blue, from that leg's own candles
 *     (oipRSComputeLegSeries): Previous Day High/Low — the day before's
 *     extremes — plus that day's OWN Open and its first 5-minute (Opening
 *     Range, 09:15–09:20 IST) candle's High and Low.
 *
 *   - Four Deciders (oipRSComputeDeciderSeries): Open/High/Low/Close Decider,
 *     each session held at the average of the PREVIOUS day's CE and PE value
 *     for that field.
 *
 * Both run through the same engine (_oipRSDayStep, and _oipRSPrevDayStep for
 * the ones that look a day back). There are no createPriceLine()s left here.
 *
 * Plus one indicator that is NOT a level line: Change in OI (Abs), the same
 * reading Dhan's "Change in Open Interest Absolute" gives. It is drawn as a
 * histogram of a leg's bar-to-bar open-interest change, and because the two
 * legs are different contracts with unrelated open interest, each gets its OWN
 * pane under the candles rather than sharing one. See the block above
 * OIP_RS_CE_STYLE_IDS.
 *
 * This block owns ALL of its data end to end: everything it draws — the CE/PE
 * candles, both volume overlays, and every pill in the stats strip above the
 * chart (Price, CE OI, PE OI, ATM, CPR, VWAP Bias, 9:18 Bias, PCR, Lot, Trend,
 * IVP) — comes from ONE request to /api/oi-profile/round-strike, polled every
 * 1 second while the market is open (see oipRSLoadData / oipRSScheduleLoop).
 * It rode along on oi_profile.js's shared candle request before; the stats
 * strip then updated on that file's slower /api/open-interest loop, which is
 * why those pills lagged the chart. Nothing here reads oi_profile.js's request
 * any more, and oi_profile.js in turn leaves the strip alone (it checks
 * window._oipRSOwnsHeader).
 *
 * Indicator show/hide and drawn Ray lines persist across a page refresh via
 * localStorage (see the "Persistence" block below — oipRSSaveIndicatorState/
 * oipRSRestoreIndicatorState, oipRSAddSavedRay/oipRSRemoveSavedRay/
 * oipRSRestoreSavedRays).
 */

'use strict';

let oipRSChart = null, oipRSCESeries = null, oipRSPESeries = null;
let oipRSVwapCESeries = null, oipRSVwapPESeries = null;
let oipRSVolumeSeries = null, oipRSBnfVolumeSeries = null;
let oipRSCurrentCEStrike = null, oipRSCurrentPEStrike = null;

// This block's OWN timeframe (#oipRSInterval in its header) — deliberately not
// oi_profile.js's oipInterval, which drives every other chart on the page from
// the Opt Prem header. The two are independent: this block is the live 1-second
// view and tends to sit on a fast timeframe, while the rest of the page only
// reloads on Refresh All and defaults to 5m. Must match the <option selected>
// on that dropdown.
let oipRSInterval = 'minute';

// ── Persistence (localStorage) — indicator show/hide + drawn rays survive a
// page refresh. Indicator state is restored before charts/series are
// created so initial visibility is correct from the first render; rays are
// restored after the first candle load so they can extend to real data.
const OIP_RS_STORAGE_KEY_INDICATORS = 'oipRS_indicators_v1';
const OIP_RS_STORAGE_KEY_RAYS = 'oipRS_rays_v1';
const OIP_RS_INDICATOR_CHECKBOX_IDS = [
    'oipRSShowVwap', 'oipRSShowVolume', 'oipRSShowBnfVolume', 'oipRSShow5mClose',
    'oipRSShowOiChgCe', 'oipRSShowOiChgPe',
    'oipRSShowCePdh', 'oipRSShowCePdl', 'oipRSShowCeOpen', 'oipRSShowCe5mHi', 'oipRSShowCe5mLo',
    'oipRSShowPePdh', 'oipRSShowPePdl', 'oipRSShowPeOpen', 'oipRSShowPe5mHi', 'oipRSShowPe5mLo',
    'oipRSShowDecOpen', 'oipRSShowDecHigh', 'oipRSShowDecLow', 'oipRSShowDecClose'
];

function oipRSSaveIndicatorState() {
    const state = {};
    OIP_RS_INDICATOR_CHECKBOX_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el) state[id] = el.checked;
    });
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_INDICATORS, JSON.stringify(state)); } catch (e) {}
}

function oipRSRestoreIndicatorState() {
    let state;
    try { state = JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_INDICATORS) || 'null'); } catch (e) { state = null; }
    if (!state) return;
    OIP_RS_INDICATOR_CHECKBOX_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el && state[id] != null) el.checked = state[id];
    });
}

function oipRSLoadSavedRays() {
    try { return JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_RAYS) || '[]'); } catch (e) { return []; }
}

function oipRSSaveRaysList(rays) {
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_RAYS, JSON.stringify(rays)); } catch (e) {}
}

function oipRSAddSavedRay(rayInfo) {
    if (!rayInfo) return;
    const rays = oipRSLoadSavedRays();
    rays.push(rayInfo);
    oipRSSaveRaysList(rays);
}

function oipRSRemoveSavedRay(rayInfo) {
    if (!rayInfo) return;
    const rays = oipRSLoadSavedRays().filter(r => !(r.time === rayInfo.time && r.price === rayInfo.price));
    oipRSSaveRaysList(rays);
}

function oipRSClearSavedRays() {
    try { localStorage.removeItem(OIP_RS_STORAGE_KEY_RAYS); } catch (e) {}
}

function oipRSRestoreSavedRays() {
    if (!oipRSChart?.addRay) return;
    oipRSLoadSavedRays().forEach(r => {
        try { oipRSChart.addRay(r.time, r.price, { color: r.color, width: r.width, lineStyle: r.lineStyle }); } catch (e) {}
    });
}

// ── Per-leg level indicators ─────────────────────────────────────────────────
// Five step series per leg (CE green, PE blue), all built from that leg's own
// option-premium candles and all re-based every session, so each day carries
// its OWN levels and the history stays on the chart:
//
//   PDH / PDL      — the day before's high / low
//   Open           — that day's own open
//   5m High / Low  — that day's own first 5-minute (Opening Range,
//                    09:15–09:20 IST) candle's high / low
//
// The first three used to be full-width createPriceLine()s, which could only
// ever state TODAY's number and drew it across every other day too. There are
// no price lines left in this block — everything is a plotted series.
//
// One colour/width/style per leg (the CE and PE pickers), one Indicators
// checkbox each.
let oipRSLegSeries = {};
const OIP_RS_CE_REF_COLOR = '#16a34a'; // green
const OIP_RS_PE_REF_COLOR = '#2563eb'; // blue
const OIP_RS_LEG_SPECS = {
    cePdh: { side: 'Ce', field: 'pdh', checkbox: 'oipRSShowCePdh', title: 'CE PDH' },
    cePdl: { side: 'Ce', field: 'pdl', checkbox: 'oipRSShowCePdl', title: 'CE PDL' },
    ceOpen: { side: 'Ce', field: 'open', checkbox: 'oipRSShowCeOpen', title: 'CE Open' },
    ce5mHi: { side: 'Ce', field: 'fiveMHi', checkbox: 'oipRSShowCe5mHi', title: 'CE 5m H' },
    ce5mLo: { side: 'Ce', field: 'fiveMLo', checkbox: 'oipRSShowCe5mLo', title: 'CE 5m L' },
    pePdh: { side: 'Pe', field: 'pdh', checkbox: 'oipRSShowPePdh', title: 'PE PDH' },
    pePdl: { side: 'Pe', field: 'pdl', checkbox: 'oipRSShowPePdl', title: 'PE PDL' },
    peOpen: { side: 'Pe', field: 'open', checkbox: 'oipRSShowPeOpen', title: 'PE Open' },
    pe5mHi: { side: 'Pe', field: 'fiveMHi', checkbox: 'oipRSShowPe5mHi', title: 'PE 5m H' },
    pe5mLo: { side: 'Pe', field: 'fiveMLo', checkbox: 'oipRSShowPe5mLo', title: 'PE 5m L' }
};
const OIP_RS_LEG_KEYS = Object.keys(OIP_RS_LEG_SPECS);
const OIP_RS_LEG_FIELDS = ['pdh', 'pdl', 'open', 'fiveMHi', 'fiveMLo'];

// ── Decider indicators ───────────────────────────────────────────────────────
// Four plotted indicator series, computed from BOTH legs at once: each holds
// the average of the previous day's CE and PE value for that OHLC field —
//   Open Decider  = (CE prev-day open  + PE prev-day open)  / 2
//   High Decider  = (CE prev-day high  + PE prev-day high)  / 2
//   Low Decider   = (CE prev-day low   + PE prev-day low)   / 2
//   Close Decider = (CE prev-day close + PE prev-day close) / 2
//
// Step series like the leg levels above — each day is drawn at the day before
// it. Data comes from oipRSComputeDeciderSeries.
//
// They blend the two legs, so there is one set of them rather than one per leg
// — both legs share this chart's single price scale, so the values sit on the
// same axis as the candles either way.
let oipRSDeciderSeries = { openD: null, highD: null, lowD: null, closeD: null };
const OIP_RS_DECIDER_KEYS = ['openD', 'highD', 'lowD', 'closeD'];
const OIP_RS_DECIDER_TITLES = { openD: 'O Dec', highD: 'H Dec', lowD: 'L Dec', closeD: 'C Dec' };
const OIP_RS_DECIDER_CHECKBOX_IDS = { openD: 'oipRSShowDecOpen', highD: 'oipRSShowDecHigh', lowD: 'oipRSShowDecLow', closeD: 'oipRSShowDecClose' };
// All four share ONE colour — they read as a group, and the axis label on each
// series says which is which — over a shared width/style, the way the CE/PE
// sections work. Pink is clear of the CE candles' green/red, the PE candles'
// violet/grey and the amber 5m-close border. The picker changes all four.
const OIP_RS_DECIDER_COLOR = '#ec4899';

// ── Change in Open Interest (Absolute) ───────────────────────────────────────
// A histogram of each option leg's own bar-to-bar OPEN INTEREST change, in
// contracts, in a pane of its own beneath the candles — the reading Dhan's
// "Change in Open Interest Absolute" indicator gives.
//
// The number is a DIFF of a level, not a per-bar flow the way volume is: the
// broker reports the leg's outstanding OI as it stood at each bar's close, so
// the bar drawn here is oi[i] - oi[i-1]. Positive means contracts were written
// into the strike over that bar, negative means positions were closed out.
//
// The data rides in on the candles themselves — every bar of ce_candles /
// pe_candles now carries an `oi` field, sourced from Fyers' history API with
// oi_flag set (see _oip_format_candles in api.py and the fyers adapter). It is
// present on derivative legs only, and ABSENT rather than zero where the broker
// gave none, which is what lets oipRSComputeOiChange below break the diff
// across a hole instead of inventing a cliff-sized bar out of a missing value.
//
// ONE SHARED PANE, TWO INDEPENDENT SCALES — both legs sit in the same space
// under the candles, CE read against the pane's RIGHT axis and PE against its
// LEFT.
//
// This replaced a pane-per-leg layout. The reason that layout existed still
// holds and is what the split scales preserve: the two legs are different
// contracts — a 24000 CE and a 24200 PE carry unrelated open interest, and
// their changes differ by orders of magnitude through the day — so on one
// SHARED scale the quieter leg flattens into the axis against the busier one's
// bars. Giving each leg its own price scale inside the one pane keeps both
// autoscaling to their own range, so they overlay in the same space and stay
// individually readable. Putting them on a single scale would be the one
// change to avoid here.
//
// Each leg is a SINGLE colour — CE red, PE green — not a per-bar up/down pair.
// Which way OI went is already stated by the side of zero a bar falls on, so
// colour is spent on the leg instead. With both legs now overlaid rather than
// stacked, that colour is the only thing telling them apart, so it matters more
// than it did.
//
// The pane is created on demand when the first leg is switched on and torn down
// when the last goes off, so the chart never carries a dead empty pane. It is
// tracked by its IPaneApi OBJECT, never by a stored index — removing a pane
// shifts every index after it, so the index is re-read (pane.paneIndex()) at
// the moment it's needed.
let oipRSOiChgPane = null;                       // IPaneApi | null — shared
let oipRSOiChgSeries = { ce: null, pe: null };   // side -> ISeriesApi | null
const OIP_RS_OI_CHG_SIDES = ['ce', 'pe'];
// CE on the pane's built-in RIGHT scale; PE on a CUSTOM OVERLAY id.
//
// PE was briefly on the built-in 'left' scale, which gave it a labelled axis but
// cost a blank gutter down the WHOLE chart: lightweight-charts reserves the left
// axis column across every pane so they stay aligned, so switching it on for the
// ΔOI pane put an empty strip beside the candles too.
//
// An overlay scale still autoscales on its own and still honours scaleMargins —
// which is all the band separation below needs — it just draws no axis of its
// own (`visible` is ignored on overlays). PE therefore trades numeric axis
// labels for the reclaimed width; its live value is still on the legend badge,
// which is where it is actually read. CE keeps the right axis, and both legs are
// in the same ±contracts order of magnitude, so that axis stays a fair ruler for
// reading PE's bars too.
const OIP_RS_OI_CHG_SCALES = { ce: 'right', pe: 'oipRSPeOiChgOverlay' };
// WHEN BOTH LEGS ARE ON, each gets its own HALF of the shared pane — CE in the
// top band, PE in the bottom. That is the CE-above-PE order the two stacked
// panes used to express by position, and the same order as the checkboxes.
//
// This is what actually keeps them apart. Separate price scales alone do NOT
// separate them on screen — they only stop one leg's range from crushing the
// other's. Without these margins both legs centre on their own zero and each
// spans the FULL pane height, so the histograms draw straight through one
// another and neither is readable.
//
// scaleMargins are fractions of the pane measured from its top and bottom, so
// {top:0.04, bottom:0.54} pins a series into the 4%–46% band and
// {top:0.54, bottom:0.04} into 54%–96%, leaving an 8% gutter between them. Each
// leg still autoscales independently inside its own band.
const OIP_RS_OI_CHG_MARGINS_SPLIT = {
    ce: { top: 0.04, bottom: 0.54 },   // top half
    pe: { top: 0.54, bottom: 0.04 }    // bottom half
};
// With only ONE leg on there is nothing to collide with, so it takes the whole
// pane rather than sitting in half of it with the other half left empty — the
// full height a single leg had back when it owned a pane to itself.
const OIP_RS_OI_CHG_MARGIN_FULL = { top: 0.12, bottom: 0.12 };
const OIP_RS_OI_CHG_SPECS = {
    ce: { checkbox: 'oipRSShowOiChgCe', title: 'CE ΔOI', defaultOn: true },
    pe: { checkbox: 'oipRSShowOiChgPe', title: 'PE ΔOI', defaultOn: true }
};
const OIP_RS_OI_CHG_STYLE_IDS = { ce: 'oipRSOiChgCeColorInp', pe: 'oipRSOiChgPeColorInp' };
// ONE colour per leg — the whole CE histogram red, the whole PE histogram green
// — rather than a per-bar up/down pair. Direction is already unambiguous from
// which side of zero a bar sits on, so tinting by sign spent colour restating
// the axis; spending it on the LEG instead means a glance at any bar says which
// contract it belongs to, which is the thing two panes of near-identical
// histograms actually make hard to tell.
//
// The red is the page's established histogram red (already the volume bars'
// down colour); the green is this block's CE reference-line green
// (OIP_RS_CE_REF_COLOR). CE takes the red and PE the green — OI building in
// calls and OI building in puts point opposite ways, so the leg colours read
// against the direction the strike's writers are leaning, not with it.
const OIP_RS_OI_CHG_DEFAULT_COLORS = { ce: '#f23645', pe: '#16a34a' };
// Height of the ΔOI pane, and the chart's height carrying none. The pane is
// added to the chart's height rather than carved out of the 575 the candles
// have, so switching a leg on never shrinks them. .oip-chart-wrap pins 575px in
// CSS, so the wrapper grows too (inline, which beats the class) or the pane
// would land outside it.
//
// 150 rather than the original 100: the two legs now share this one pane and
// split it in half (OIP_RS_OI_CHG_MARGINS_SPLIT), so at 100 each leg had only
// ~42px of drawing room. 150 gives each band ~63px while the whole chart still
// comes to 725 with both legs on — under the 775 the two stacked panes used to
// reach. Nudge this one number to taste; the candles are the separate constant
// below and are unaffected either way.
const OIP_RS_OI_CHG_PANE_HEIGHT = 150;
// The candles' own height. Raised 575 -> 595 so the chart is 20px taller in
// EVERY state, whether or not a ΔOI leg is showing — putting the 20px on the
// pane instead would only have grown the chart while an indicator was on.
//
// This is deliberately NOT the 575 in .oip-chart-wrap's CSS: that class is
// shared with the main OI and Premium charts ("both OI and Premium charts are
// same height") and must stay where it is. The Round Strike wrapper is sized
// inline by oipRSSetChartHeight, which beats the class.
// Matches --oip-replay-chart-h in static/css/oi_profile.css — the index chart
// above and this one are read as a pair and are meant to be the same height.
const OIP_RS_BASE_CHART_HEIGHT = 426;

function oipRSSetChartHeight(px) {
    try { oipRSChart?.chart?.applyOptions({ height: px }); } catch (e) {}
    const wrap = document.getElementById('oipRSCombinedChartWrap');
    if (wrap) wrap.style.height = `${px}px`;
}

// Grows/shrinks the chart by the one ΔOI pane, then re-pins it to its fixed
// height — lightweight-charts redistributes pane heights when the chart's own
// height changes, so the pin has to come after.
//
// Both legs now share this single pane, so the added height is a flat
// OIP_RS_OI_CHG_PANE_HEIGHT whether one leg is showing or both — it no longer
// scales with the number of legs. Neither constant changed: the candles keep
// their 575 and a ΔOI pane is still 100.
function oipRSApplyOiChgChartHeight() {
    const extra = oipRSOiChgPane ? OIP_RS_OI_CHG_PANE_HEIGHT : 0;
    oipRSSetChartHeight(OIP_RS_BASE_CHART_HEIGHT + extra);
    if (oipRSOiChgPane) {
        try { oipRSOiChgPane.setHeight(OIP_RS_OI_CHG_PANE_HEIGHT); } catch (e) {}
    }
}

function oipRSOiChgColor(side) {
    return document.getElementById(OIP_RS_OI_CHG_STYLE_IDS[side])?.value
        || OIP_RS_OI_CHG_DEFAULT_COLORS[side];
}

function oipRSOiChgIsOn(side) {
    return document.getElementById(OIP_RS_OI_CHG_SPECS[side].checkbox)?.checked
        ?? OIP_RS_OI_CHG_SPECS[side].defaultOn;
}

// Bar-to-bar OI change for one leg. Bars WITHOUT an `oi` (a non-derivative leg,
// or the locally rebuilt synthetic tail) don't just get skipped — they reset the
// running previous value, so the next real bar starts a fresh diff instead of
// subtracting across the hole and drawing a spike that never happened.
//
// The first bar of the series has nothing to diff against and is therefore
// omitted, not drawn at zero: a zero bar would claim OI held flat, which is a
// different statement from "not known yet".
function oipRSComputeOiChange(candles) {
    const bars = [];
    let prevOi = null;
    (candles || []).forEach(c => {
        const oi = (c && c.oi != null) ? Number(c.oi) : null;
        if (oi == null || !Number.isFinite(oi)) { prevOi = null; return; }
        if (prevOi != null) bars.push({ time: Number(c.time), value: oi - prevOi });
        prevOi = oi;
    });
    return bars;
}

// The bars carry no colour of their own — the whole leg is one colour, so it
// lives on the SERIES. That is what makes a colour change a bare applyOptions
// below instead of a re-push of every bar, and why this needs no cache of the
// last-drawn bars the way the volume overlays do (_oipVolBarCache in
// oi_indicators.js, where colour is per-bar because it tracks candle direction).
function oipRSPaintOiChangeBars(side, bars) {
    const series = oipRSOiChgSeries[side];
    if (!series) return;
    try { series.setData(bars.map(b => ({ time: b.time, value: b.value }))); } catch (e) {}
}

function oipRSApplyOiChgColors() {
    OIP_RS_OI_CHG_SIDES.forEach(side => {
        try { oipRSOiChgSeries[side]?.applyOptions({ color: oipRSOiChgColor(side) }); } catch (e) {}
    });
}

// Puts each open leg in its band. Re-run on every open and close, because the
// answer depends on how many legs are showing: two legs split the pane in half,
// a lone leg takes all of it.
//
// `visible` is set only for CE, the one leg on a built-in scale. PE rides an
// overlay id, where lightweight-charts ignores `visible` outright — passing it
// there would read as a promise of an axis that never appears.
function oipRSApplyOiChgMargins() {
    const open = OIP_RS_OI_CHG_SIDES.filter(s => oipRSOiChgSeries[s]);
    const split = open.length > 1;
    open.forEach(side => {
        const opts = {
            scaleMargins: split ? OIP_RS_OI_CHG_MARGINS_SPLIT[side] : OIP_RS_OI_CHG_MARGIN_FULL
        };
        if (side === 'ce') opts.visible = true;
        try { oipRSOiChgSeries[side].priceScale()?.applyOptions(opts); } catch (e) {}
    });
}

// The shared pane, created on first use. Both legs live here, so this runs once
// however many legs are switched on.
function oipRSEnsureOiChgPane() {
    const chart = oipRSChart?.chart;
    if (!chart) return null;
    if (!oipRSOiChgPane) oipRSOiChgPane = chart.addPane();
    return oipRSOiChgPane;
}

// Adds ONE leg's histogram to the shared pane, on that leg's own price scale.
// Called lazily (see oipRSSyncOiChgPane) rather than from oipRSInitCharts, so a
// leg the user has switched off never carries a dead series.
function oipRSCreateOiChgSeries(side) {
    const chart = oipRSChart?.chart;
    if (!chart || oipRSOiChgSeries[side]) return;
    try {
        const pane = oipRSEnsureOiChgPane();
        if (!pane) return;
        const series = chart.addSeries(LightweightCharts.HistogramSeries, {
            priceFormat: { type: 'volume' },
            priceScaleId: OIP_RS_OI_CHG_SCALES[side],
            title: OIP_RS_OI_CHG_SPECS[side].title,
            color: oipRSOiChgColor(side),
            priceLineVisible: false,
            crosshairMarkerVisible: false
        }, pane.paneIndex());
        oipRSOiChgSeries[side] = series;
        // Band assignment depends on how many legs are open, so it runs after
        // the series is registered above and again on every open/close — see
        // oipRSApplyOiChgMargins.
        oipRSApplyOiChgMargins();
        // The new series carries the bare default title; clear the memo so
        // oipRSSetOiChgTitles re-stamps the strike onto it (a leg the user
        // toggles off and back on would otherwise keep the unnamed axis).
        _oipRSOiChgTitleFor[side] = null;
        oipRSApplyOiChgChartHeight();
    } catch (e) {
        console.warn(`[RoundStrike] ${side.toUpperCase()} Chg in OI series could not be created:`, e);
    }
}

// Drops one leg, and the shared pane with it once the last leg goes.
function oipRSDestroyOiChgSeries(side) {
    const chart = oipRSChart?.chart;
    const series = oipRSOiChgSeries[side];
    if (!chart || !series) return;
    try { chart.removeSeries(series); } catch (e) {}
    oipRSOiChgSeries[side] = null;

    // Only tear the pane down when NEITHER leg is left — the other leg is still
    // drawing in it otherwise.
    const anyLeft = OIP_RS_OI_CHG_SIDES.some(s => oipRSOiChgSeries[s]);
    if (!anyLeft && oipRSOiChgPane) {
        // The index is read HERE, not stored: panes above may have shifted.
        // Guarded rather than checked because lightweight-charts may already
        // have dropped the now-empty pane itself, and removing it twice throws.
        try {
            const idx = oipRSOiChgPane.paneIndex();
            if (idx > 0 && chart.panes()?.length > idx) chart.removePane(idx);
        } catch (e) {}
        oipRSOiChgPane = null;
    }
    // The surviving leg (if any) goes back to the full pane now that it has the
    // space to itself.
    oipRSApplyOiChgMargins();
    oipRSApplyOiChgChartHeight();
}

// Brings both legs in line with their checkboxes — each added or torn down on
// its own within the shared pane — and repaints from the candles the last
// render parked, so a toggle takes effect immediately rather than waiting for
// the next poll.
//
// No pane ordering step any more: with one shared pane there is nothing to
// order, and CE-above-PE is now expressed by the axis each leg is read against
// (CE right, PE left) rather than by vertical position.
function oipRSSyncOiChgPane() {
    if (!oipRSChart?.chart) return;
    OIP_RS_OI_CHG_SIDES.forEach(side => {
        if (oipRSOiChgIsOn(side)) oipRSCreateOiChgSeries(side);
        else oipRSDestroyOiChgSeries(side);
    });
    // Unconditional, and load-bearing on the both-legs-off path: the create and
    // destroy helpers each size the chart themselves, but destroy returns early
    // when there was no series to remove — so a page opening with BOTH legs off
    // would never size the wrapper at all, leaving it on .oip-chart-wrap's
    // hardcoded 575px CSS instead of OIP_RS_BASE_CHART_HEIGHT. Harmless while
    // those two numbers happened to be equal; wrong the moment they differ.
    oipRSApplyOiChgChartHeight();
    oipRSUpdateOiChangeSeries(oipRSLastCeData, oipRSLastPeData);
}

// Pushes each leg's bars into its series. Safe to call on every render — a
// no-op for a leg that isn't switched on.
function oipRSUpdateOiChangeSeries(ceData, peData) {
    const data = { ce: ceData, pe: peData };
    OIP_RS_OI_CHG_SIDES.forEach(side => {
        if (oipRSOiChgSeries[side]) oipRSPaintOiChangeBars(side, oipRSComputeOiChange(data[side]));
    });
}

// Names each leg with the contract it is actually showing — "24000 CE ΔOI"
// rather than a bare "CE ΔOI" — so a glance says which strike the bars belong
// to, the way Dhan's own pane header does. Skipped unless the strike changed:
// this runs on a 1-second poll and applyOptions redraws.
//
// Now that both legs share one pane these two labels sit side by side in the
// same legend, which is what makes the strike in each one worth having.
let _oipRSOiChgTitleFor = { ce: null, pe: null };

function oipRSSetOiChgTitles(ceStrike, peStrike) {
    const strikes = { ce: ceStrike, pe: peStrike };
    OIP_RS_OI_CHG_SIDES.forEach(side => {
        const series = oipRSOiChgSeries[side];
        const strike = strikes[side];
        if (!series || !strike || _oipRSOiChgTitleFor[side] === strike) return;
        try {
            series.applyOptions({ title: `${strike} ${OIP_RS_OI_CHG_SPECS[side].title}` });
            _oipRSOiChgTitleFor[side] = strike;
        } catch (e) {}
    });
}

function oipRSOnOiChgToggle() {
    oipRSSyncOiChgPane();
    oipRSSaveIndicatorState();
}

function oipRSOnOiChgColorChange() {
    oipRSApplyOiChgColors();
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}

// Per-side (CE / PE) style pickers — one color/width/style applies to ALL 5
// lines of that side at once (PDH, PDL, Open, 5m Hi, 5m Lo), same idea as the
// Ray tool's style pickers. Persisted alongside indicator show/hide state.
const OIP_RS_CE_STYLE_IDS = { color: 'oipRSCeLineColorInp', width: 'oipRSCeLineWidthSel', style: 'oipRSCeLineStyleSel' };
const OIP_RS_PE_STYLE_IDS = { color: 'oipRSPeLineColorInp', width: 'oipRSPeLineWidthSel', style: 'oipRSPeLineStyleSel' };
const OIP_RS_DEC_STYLE_IDS = { color: 'oipRSDecLineColorInp', width: 'oipRSDecLineWidthSel', style: 'oipRSDecLineStyleSel' };
const OIP_RS_STORAGE_KEY_LINESTYLE = 'oipRS_lineStyle_v1';

function oipRSLineStyleFromPickers(ids, fallbackColor) {
    return {
        color: document.getElementById(ids.color)?.value || fallbackColor,
        width: parseInt(document.getElementById(ids.width)?.value, 10) || 1,
        lineStyle: parseInt(document.getElementById(ids.style)?.value, 10) || 0
    };
}

// Style shared by all four Decider lines.
function oipRSDeciderStyle() {
    return oipRSLineStyleFromPickers(OIP_RS_DEC_STYLE_IDS, OIP_RS_DECIDER_COLOR);
}

// Bumped when a DEFAULT in this popup changes in a way a previously saved state
// would otherwise mask. Everything here is persisted the moment any one picker
// moves, so a returning user carries a full snapshot of the OLD defaults — and a
// new default would never be seen. The version lets a restore drop just the one
// stale field instead of discarding the user's other, deliberate choices.
//
//   1 -> 2: the Decider lines defaulted to Dashed and now default to Solid.
const OIP_RS_LINESTYLE_STATE_VERSION = 2;

function oipRSSaveLineStyleState() {
    const state = {
        v: OIP_RS_LINESTYLE_STATE_VERSION,
        ce: oipRSLineStyleFromPickers(OIP_RS_CE_STYLE_IDS, OIP_RS_CE_REF_COLOR),
        pe: oipRSLineStyleFromPickers(OIP_RS_PE_STYLE_IDS, OIP_RS_PE_REF_COLOR),
        dec: oipRSDeciderStyle(),
        // 5m Close Border has a colour only (no width/style — see oipRSMark5mCloseBorders).
        fiveMClose: document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT,
        // Chg in OI carries one colour per leg and nothing else — a histogram
        // bar has no width or dash style to set.
        oiChg: { ce: oipRSOiChgColor('ce'), pe: oipRSOiChgColor('pe') }
    };
    try { localStorage.setItem(OIP_RS_STORAGE_KEY_LINESTYLE, JSON.stringify(state)); } catch (e) {}
}

function oipRSRestoreLineStyleState() {
    let state;
    try { state = JSON.parse(localStorage.getItem(OIP_RS_STORAGE_KEY_LINESTYLE) || 'null'); } catch (e) { state = null; }
    if (!state) return;
    const version = state.v || 1;
    const apply = (ids, saved, skipStyle = false) => {
        if (!saved) return;
        const c = document.getElementById(ids.color); if (c && saved.color) c.value = saved.color;
        const w = document.getElementById(ids.width); if (w && saved.width != null) w.value = saved.width;
        const s = document.getElementById(ids.style);
        if (s && !skipStyle && saved.lineStyle != null) s.value = saved.lineStyle;
    };
    apply(OIP_RS_CE_STYLE_IDS, state.ce);
    apply(OIP_RS_PE_STYLE_IDS, state.pe);
    // A pre-v2 snapshot carries the old Dashed default for the Deciders, saved
    // whether or not the user ever chose it. Keep their colour and width, but let
    // the line style fall through to the markup's new Solid default.
    apply(OIP_RS_DEC_STYLE_IDS, state.dec, version < 2);
    const f = document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID);
    if (f && state.fiveMClose) f.value = state.fiveMClose;
    ['ce', 'pe'].forEach(side => {
        // Skips the object an earlier build saved here ({up, dn}) — that shape
        // predates the one-colour-per-leg histogram and has no single colour to
        // restore, so those legs fall back to their green/red defaults.
        const saved = state.oiChg?.[side];
        if (typeof saved !== 'string') return;
        const inp = document.getElementById(OIP_RS_OI_CHG_STYLE_IDS[side]);
        if (inp) inp.value = saved;
    });
}

// Reflects the current CE/PE/Decider colors onto the indicator checkbox labels
// in the Indicators popup, so the swatch text always matches what's drawn.
function oipRSUpdateCheckboxSpanColors() {
    const ceColor = document.getElementById(OIP_RS_CE_STYLE_IDS.color)?.value || OIP_RS_CE_REF_COLOR;
    const peColor = document.getElementById(OIP_RS_PE_STYLE_IDS.color)?.value || OIP_RS_PE_REF_COLOR;
    ['oipRSShowCePdh', 'oipRSShowCePdl', 'oipRSShowCeOpen', 'oipRSShowCe5mHi', 'oipRSShowCe5mLo'].forEach(id => {
        const span = document.getElementById(id)?.nextElementSibling;
        if (span) span.style.color = ceColor;
    });
    ['oipRSShowPePdh', 'oipRSShowPePdl', 'oipRSShowPeOpen', 'oipRSShowPe5mHi', 'oipRSShowPe5mLo'].forEach(id => {
        const span = document.getElementById(id)?.nextElementSibling;
        if (span) span.style.color = peColor;
    });
    const decColor = oipRSDeciderStyle().color;
    OIP_RS_DECIDER_KEYS.forEach(key => {
        const span = document.getElementById(OIP_RS_DECIDER_CHECKBOX_IDS[key])?.nextElementSibling;
        if (span) span.style.color = decColor;
    });
    const fiveMSpan = document.getElementById('oipRSShow5mClose')?.nextElementSibling;
    if (fiveMSpan) fiveMSpan.style.color = document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT;
    OIP_RS_OI_CHG_SIDES.forEach(side => {
        const span = document.getElementById(OIP_RS_OI_CHG_SPECS[side].checkbox)?.nextElementSibling;
        if (span) span.style.color = oipRSOiChgColor(side);
    });
}

// Style shared by all 5 of a leg's level series.
function oipRSLegStyle(side) {
    return side === 'Ce'
        ? oipRSLineStyleFromPickers(OIP_RS_CE_STYLE_IDS, OIP_RS_CE_REF_COLOR)
        : oipRSLineStyleFromPickers(OIP_RS_PE_STYLE_IDS, OIP_RS_PE_REF_COLOR);
}

// Restyles one leg's level series in place — no refetch, data is unchanged.
function oipRSApplyLegStyleLive(side, style) {
    OIP_RS_LEG_KEYS.filter(key => OIP_RS_LEG_SPECS[key].side === side).forEach(key => {
        try {
            oipRSLegSeries[key]?.applyOptions({ color: style.color, lineWidth: style.width, lineStyle: style.lineStyle });
        } catch (e) {}
    });
}

function oipRSOnCeStyleChange() {
    oipRSApplyLegStyleLive('Ce', oipRSLegStyle('Ce'));
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}

function oipRSOnPeStyleChange() {
    oipRSApplyLegStyleLive('Pe', oipRSLegStyle('Pe'));
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}

// Show/hide, straight off the ten CE/PE level checkboxes.
function oipRSSyncLegVisibility() {
    OIP_RS_LEG_KEYS.forEach(key => {
        const visible = document.getElementById(OIP_RS_LEG_SPECS[key].checkbox)?.checked ?? true;
        try { oipRSLegSeries[key]?.applyOptions({ visible }); } catch (e) {}
    });
}

// Restyles all four Decider series in place — no refetch, data is unchanged.
function oipRSOnDeciderStyleChange() {
    const style = oipRSDeciderStyle();
    OIP_RS_DECIDER_KEYS.forEach(key => {
        try {
            oipRSDeciderSeries[key]?.applyOptions({ color: style.color, lineWidth: style.width, lineStyle: style.lineStyle });
        } catch (e) {}
    });
    oipRSUpdateCheckboxSpanColors();
    oipRSSaveLineStyleState();
}

// Show/hide, straight off the four checkboxes.
function oipRSSyncDeciderVisibility() {
    OIP_RS_DECIDER_KEYS.forEach(key => {
        const visible = document.getElementById(OIP_RS_DECIDER_CHECKBOX_IDS[key])?.checked ?? true;
        try { oipRSDeciderSeries[key]?.applyOptions({ visible }); } catch (e) {}
    });
}

// Bar duration (minutes) per interval — used to find which loaded bar(s)
// OVERLAP the 09:15–09:20 Opening Range window rather than start exactly
// inside it (matters for coarser intervals like 15m/30m).
const OIP_RS_BAR_MINUTES = { '30second': 0.5, minute: 1, '2minute': 2, '3minute': 3, '5minute': 5, '15minute': 15, '30minute': 30 };

function oipRSComputeStrikes(openPrice) {
    const near50 = Math.round(openPrice / 50) * 50;
    if (near50 % 100 === 0) {
        return { ceStrike: near50 - 100, peStrike: near50 + 100 };
    }
    return { ceStrike: near50 - 50, peStrike: near50 + 50 };
}

// ── Per-day step series (leg levels + Deciders) ──────────────────────────────
// The trading-day key for a bar. "Fake IST Epoch" timestamps, so the UTC
// getters already read as IST clock time (the convention _oipGroupCandlesByDay
// relies on too).
function _oipRSDayKey(time) {
    const d = new Date(time * 1000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}

// One trading day's OHLC from its bars, or null when the day has none usable.
function _oipRSDayOHLC(dayCandles) {
    if (!dayCandles || !dayCandles.length) return null;
    const o = Number(dayCandles[0].open);
    const c = Number(dayCandles[dayCandles.length - 1].close);
    const h = Math.max(...dayCandles.map(x => Number(x.high)));
    const l = Math.min(...dayCandles.map(x => Number(x.low)));
    return [o, h, l, c].some(v => !isFinite(v)) ? null : { o, h, l, c };
}

// That day's first 5-minute (Opening Range, 09:15–09:20 IST) high/low, or null
// when the day has no bar in the window. Bars are matched by OVERLAP rather
// than by starting inside it, so coarser intervals (15m/30m) still resolve.
// "Fake IST Epoch" timestamps, so the UTC getters already read as IST clock
// time (the convention _oipGroupCandlesByDay relies on too).
function _oipRSOpeningRange(dayCandles) {
    const barMin = OIP_RS_BAR_MINUTES[oipRSInterval] || 1;
    const WIN_START = 9 * 60 + 15, WIN_END = 9 * 60 + 20;
    const w = (dayCandles || []).filter(c => {
        const d = new Date(c.time * 1000);
        const startMin = d.getUTCHours() * 60 + d.getUTCMinutes();
        return startMin < WIN_END && (startMin + barMin) > WIN_START;
    });
    if (!w.length) return null;
    const hi = Math.max(...w.map(c => Number(c.high)));
    const lo = Math.min(...w.map(c => Number(c.low)));
    return (isFinite(hi) && isFinite(lo)) ? { hi, lo } : null;
}

// The engine behind every step indicator here. `dayVal` maps a day key to that
// day's values ({key: number}); it is painted flat across each day's bars on
// `axisCandles`. Returns one {time, value} array per key; a day with no value
// plots nothing, so a gap stays visible as a gap.
function _oipRSDayStep(axisCandles, dayVal, keys) {
    const out = {};
    keys.forEach(k => out[k] = []);
    axisCandles.forEach(candle => {
        const v = dayVal[_oipRSDayKey(candle.time)];
        if (!v) return;
        keys.forEach(k => { if (v[k] != null) out[k].push({ time: candle.time, value: v[k] }); });
    });
    return out;
}

// Same, one day later: each session is held at the PREVIOUS day's numbers —
// the "previous day's value, flat across the next session" convention shared
// with oipCalculatePrevDayCloseAvg in oi_indicators.js. A day whose
// predecessor has no value plots nothing rather than reaching further back.
function _oipRSPrevDayStep(axisCandles, order, dayVal, keys) {
    const prevVal = {};
    for (let i = 1; i < order.length; i++) {
        const prev = dayVal[order[i - 1]];
        if (prev) prevVal[order[i]] = prev;
    }
    return _oipRSDayStep(axisCandles, prevVal, keys);
}

// One leg's five level series, each re-based per session:
//   pdh / pdl              — the day BEFORE's high / low
//   open, fiveMHi, fiveMLo — that day's OWN open and opening-range high/low
// Returns {pdh, pdl, open, fiveMHi, fiveMLo}, each an array of {time, value}.
function oipRSComputeLegSeries(candles) {
    const empty = {};
    OIP_RS_LEG_FIELDS.forEach(f => empty[f] = []);
    if (!candles?.length || typeof _oipGroupCandlesByDay !== 'function') return empty;
    const { map, order } = _oipGroupCandlesByDay(candles);

    const dayHiLo = {};   // shifted forward a day -> PDH/PDL
    const dayOwn = {};    // stays on its own day -> Open, 5m Hi/Lo
    order.forEach(day => {
        const d = _oipRSDayOHLC(map[day]);
        if (!d) return;
        dayHiLo[day] = { pdh: d.h, pdl: d.l };
        const or = _oipRSOpeningRange(map[day]);
        dayOwn[day] = { open: d.o, fiveMHi: or?.hi ?? null, fiveMLo: or?.lo ?? null };
    });

    return {
        ..._oipRSPrevDayStep(candles, order, dayHiLo, ['pdh', 'pdl']),
        ..._oipRSDayStep(candles, dayOwn, ['open', 'fiveMHi', 'fiveMLo'])
    };
}

// The four Decider step series — each session held at the average of the
// PREVIOUS day's CE and PE value for that field (open = the day's first bar's
// open, high/low = the day's extremes, close = its last bar's close).
//
// Returns {openD, highD, lowD, closeD}, each an array of {time, value} on the
// CE candles' timestamps. A day is skipped where either leg is missing it — a
// half average would be a plain CE (or PE) line wearing the Decider's colour.
function oipRSComputeDeciderSeries(ceCandles, peCandles) {
    const empty = { openD: [], highD: [], lowD: [], closeD: [] };
    if (!ceCandles?.length || !peCandles?.length || typeof _oipGroupCandlesByDay !== 'function') return empty;

    const ce = _oipGroupCandlesByDay(ceCandles);
    const pe = _oipGroupCandlesByDay(peCandles);

    const dayVal = {};
    ce.order.forEach(day => {
        const c = _oipRSDayOHLC(ce.map[day]);
        const p = _oipRSDayOHLC(pe.map[day]);
        if (!c || !p) return;
        dayVal[day] = {
            openD: (c.o + p.o) / 2,
            highD: (c.h + p.h) / 2,
            lowD: (c.l + p.l) / 2,
            closeD: (c.c + p.c) / 2
        };
    });
    return _oipRSPrevDayStep(ceCandles, ce.order, dayVal, OIP_RS_DECIDER_KEYS);
}

function oipRSInitCharts() {
    if (typeof TradingViewChart === 'undefined') return;

    oipRSChart = TradingViewChart.create({
        containerId: 'oipRSCombinedChart', data: [], type: 'COMBINED',
        // Getter (not a plain string) — oipRSInterval changes via this block's
        // own TF dropdown after the chart is created, and the ray tool's reach
        // needs the CURRENT interval, not the one at attach time (same
        // reasoning as the main OI chart's ray tool).
        isCombined: true, timeframe: () => oipRSInterval,
        options: { height: OIP_RS_BASE_CHART_HEIGHT },
        // The DeltaOI panes run to millions of contracts, which the default 2dp
        // formatter rendered as "-2500000.00" — six digits of noise on a narrow
        // axis. Compact ticks only kick in at a lakh, so the premium axis on the
        // candle pane (~100-200) is untouched.
        compactPriceAxis: true,
        // Narrower than the 85 default: with compact ticks the widest label on
        // this chart is a premium like "191.60" or a DeltaOI like "-25L", so the
        // extra width was empty gutter between the plot and the axis.
        priceAxisWidth: 62,
        onRayDrawn: oipRSRayDisarm,
        onRayRemoved: oipRSRemoveSavedRay
    });
    oipRSCESeries = oipRSChart.ceSeries || oipRSChart.series;
    oipRSPESeries = oipRSChart.peSeries;

    // Match the bar width of whatever this block sits next to.
    //
    // TradingViewChart defaults to barSpacing 4 — "half of OI chart spacing for
    // compact option view" — which is right on OI Profile, where this chart is
    // one of several option panes that all use 4. On Replay it is stacked under
    // the index chart, which draws at 8, so the pair rendered at two different
    // horizontal scales and a crosshair synced by TIME landed at visibly
    // different x positions on each.
    if (oipRSIsHistorical()) {
        try { oipRSChart.chart.timeScale().applyOptions({ barSpacing: 8, rightOffset: 20 }); } catch (e) {}
    }

    // This chart is deliberately NOT part of the shared pan/zoom sync web that
    // the OI Profile and Opt Prem charts form (see oi_profile_init.js). That web
    // syncs LOGICAL ranges — bar indices — which only means the same thing when
    // every chart is on the same bar grid. This block has its own TF dropdown
    // now, so a pan here would scroll the others to the wrong place whenever the
    // two timeframes differ. Same reason Fixed 24000 Monthly stays out of it.
    //
    // It does join the crosshair-sync web below: that one matches on TIME, so it
    // works across mismatched bar grids.
    //
    // On Replay it also pairs with the index chart for zoom and pan (the block
    // further down, and its mirror in oi_replay.js). That pairing sidesteps the
    // logical-range problem above rather than ignoring it: matching bar spacing
    // is only used when the two TF dropdowns agree, and mismatched grids fall
    // back to matching the visible TIME range — the same basis the crosshair
    // sync uses.
    ['mouseenter', 'touchstart'].forEach(evt => {
        document.getElementById('oipRSCombinedChart')?.addEventListener(evt, () => { window._oipActiveChartId = 'rs'; }, { passive: true });
    });
    // Crosshair hover — reuses oi_profile_init.js's
    // syncCrosshair via window._oipSyncCrosshair. oipRSCESeries is this
    // chart's anchor series for price lookup (same role oipIntrinsicSeries
    // plays for Intrinsic).
    if (oipRSChart?.chart && typeof window._oipSyncCrosshair === 'function') {
        oipRSChart.chart.subscribeCrosshairMove(param => {
            if (window._oipActiveChartId !== 'rs') return;
            if (oipOIChart && oipOISeries) window._oipSyncCrosshair(oipRSChart.chart, oipOIChart, param, oipOISeries);
            if (oipIntrinsicChart?.chart && oipIntrinsicSeries) window._oipSyncCrosshair(oipRSChart.chart, oipIntrinsicChart.chart, param, oipIntrinsicSeries);
            if (oipCEChart?.chart && oipCESeries) window._oipSyncCrosshair(oipRSChart.chart, oipCEChart.chart, param, oipCESeries);
            if (oipPEChart?.chart && oipPESeries) window._oipSyncCrosshair(oipRSChart.chart, oipPEChart.chart, param, oipPESeries);
            // typeof-guarded: Fixed 24000 Monthly is an OI Profile chart, and this
            // file is loaded on Replay too, where oi_profile.js (where it lives) is not.
            if (typeof oipFixedChart !== 'undefined' && oipFixedChart?.chart && oipFixedCeSeries) window._oipSyncCrosshair(oipRSChart.chart, oipFixedChart.chart, param, oipFixedCeSeries);
        });
    }

    // Zoom / pan, the reverse of the index chart's own subscription. Guarded on
    // _oipSyncTimeScale existing, the same way the crosshair block above is:
    // Replay defines it (oi_replay.js), OI Profile does not — that page already
    // range-syncs its index / Intrinsic / CE / PE panes among themselves, and
    // pulling this chart into that is a separate question.
    if (oipRSChart?.chart && typeof window._oipSyncTimeScale === 'function') {
        oipRSChart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
            if (window._oipSuppressRangeSync || window._oipActiveChartId !== 'rs') return;
            try {
                if (!oipOIChart) return;
                window._oipSyncTimeScale(oipRSChart.chart, oipOIChart, oipRSInterval === oipInterval);
            } catch (e) {}
        });
    }

    const showVwap = document.getElementById('oipRSShowVwap')?.checked ?? true;
    // crosshairMarkerVisible:false on every line series here — see addStepSeries
    // below for why.
    oipRSVwapCESeries = oipRSChart.chart.addSeries(LightweightCharts.LineSeries, {
        color: '#1b9981', lineWidth: 1, visible: showVwap,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null
    });
    oipRSVwapPESeries = oipRSChart.chart.addSeries(LightweightCharts.LineSeries, {
        color: '#8b5cf6', lineWidth: 1, visible: showVwap,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null
    });

    document.getElementById('oipRSShowVwap')?.addEventListener('change', (e) => {
        const v = e.target.checked;
        oipRSVwapCESeries?.applyOptions({ visible: v });
        oipRSVwapPESeries?.applyOptions({ visible: v });
        oipRSSaveIndicatorState();
    });

    // Every level line on this chart — the two legs' 5 each plus the 4 Deciders
    // — is a step series, fed per render from that leg's (or both legs')
    // candles. autoscaleInfoProvider is left at null (as the VWAP lines above
    // do) so a level sitting far from today's premium can't stretch the price
    // scale and squash the candles.
    //
    // crosshairMarkerVisible:false: lightweight-charts defaults it ON, so every
    // one of these lines drew a filled dot where the crosshair met it. With ~14
    // near-horizontal levels stacked across the pane that is a column of large
    // dots following the cursor, obscuring the candles it is meant to help read.
    // The crosshair line and the axis labels already say where the cursor is.
    const addStepSeries = (style, title, visible) => oipRSChart.chart.addSeries(LightweightCharts.LineSeries, {
        color: style.color, lineWidth: style.width, lineStyle: style.lineStyle, title, visible,
        priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null
    });

    const legStyle = { Ce: oipRSLegStyle('Ce'), Pe: oipRSLegStyle('Pe') };
    OIP_RS_LEG_KEYS.forEach(key => {
        const spec = OIP_RS_LEG_SPECS[key];
        oipRSLegSeries[key] = addStepSeries(
            legStyle[spec.side], spec.title,
            document.getElementById(spec.checkbox)?.checked ?? true);
    });

    const decStyle = oipRSDeciderStyle();
    OIP_RS_DECIDER_KEYS.forEach(key => {
        oipRSDeciderSeries[key] = addStepSeries(
            decStyle, OIP_RS_DECIDER_TITLES[key],
            document.getElementById(OIP_RS_DECIDER_CHECKBOX_IDS[key])?.checked ?? true);
    });

    // Volume histograms — the same future volumes used on the main OI Profile
    // chart, matched to this block's own candle timestamps. Unlike the other
    // blocks, this one splits the two: Nifty stays on the bottom-pinned hidden
    // scale (as everywhere else), while Banknifty gets its own scale pinned to
    // the TOP of the pane (bnfOnTop) so the two read separately instead of
    // blending into each other — see oipAddVolumeSeriesPair in oi_indicators.js.
    const showVolume = document.getElementById('oipRSShowVolume')?.checked ?? true;
    const showBnfVolume = document.getElementById('oipRSShowBnfVolume')?.checked ?? false;
    [oipRSVolumeSeries, oipRSBnfVolumeSeries] = oipAddVolumeSeriesPair(
        oipRSChart.chart, 'oipRSVolume', showVolume, showBnfVolume, true);

    // Switching an overlay ON needs a refetch on the historical block: its bars
    // were never requested (see oipRSApiUrl), so there is nothing parked to make
    // visible. Switching OFF just hides what is already there.
    const onVolToggle = (series) => (e) => {
        series()?.applyOptions({ visible: e.target.checked });
        oipRSSaveIndicatorState();
        if (e.target.checked && oipRSIsHistorical()) oipRSRequestReload();
    };
    document.getElementById('oipRSShowVolume')
        ?.addEventListener('change', onVolToggle(() => oipRSVolumeSeries));
    document.getElementById('oipRSShowBnfVolume')
        ?.addEventListener('change', onVolToggle(() => oipRSBnfVolumeSeries));

    // Keep chart width in sync with its wrapper (same pattern as Opt Prem).
    const wrap = document.getElementById('oipRSCombinedChartWrap');
    if (wrap && oipRSChart?.chart) {
        new ResizeObserver(() => {
            if (wrap.clientWidth) oipRSChart.chart.applyOptions({ width: wrap.clientWidth });
        }).observe(wrap);
    }
}

// Disarms the ray tool and resets the toolbar button — called after a ray is
// drawn (single-shot arm, matches Opt Prem's Ray tool behavior). `rayInfo`
// ({time, price, color, width, lineStyle}) is persisted so the ray survives
// a page refresh (see oipRSRestoreSavedRays, called once on init).
function oipRSRayDisarm(rayInfo) {
    oipRSChart?.setRayMode(false);
    document.getElementById('oipRSRayToolBtn')?.classList.remove('oip-btn--armed');
    document.getElementById('oipRSRayOptionsPopup')?.classList.add('hidden');
    oipRSAddSavedRay(rayInfo);
}

// Color/width/style pickers set the look of the NEXT ray only — read fresh
// on each arm, so changing them mid-session doesn't touch rays already drawn.
function oipRSRayStyleFromPickers() {
    return {
        color: document.getElementById('oipRSRayColorInp')?.value || '#f33968',
        width: parseInt(document.getElementById('oipRSRayWidthSel')?.value, 10) || 2,
        lineStyle: parseInt(document.getElementById('oipRSRayStyleSel')?.value, 10) ?? 1
    };
}

function oipRSInitRayTool() {
    const rayBtn = document.getElementById('oipRSRayToolBtn');
    const clearBtn = document.getElementById('oipRSRayClearBtn');
    const popup = document.getElementById('oipRSRayOptionsPopup');
    if (rayBtn) {
        rayBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willArm = !rayBtn.classList.contains('oip-btn--armed');
            oipRSChart?.setRayMode(willArm, willArm ? oipRSRayStyleFromPickers() : undefined);
            rayBtn.classList.toggle('oip-btn--armed', willArm);
            popup?.classList.toggle('hidden', !willArm);
        });
    }
    // Live-restyle the armed (not-yet-placed) ray as the pickers change.
    if (popup) {
        popup.addEventListener('change', () => {
            if (rayBtn?.classList.contains('oip-btn--armed')) {
                oipRSChart?.setRayMode(true, oipRSRayStyleFromPickers());
            }
        });
    }
    // Clicking outside the popup/button while armed cancels ray mode.
    document.addEventListener('click', (e) => {
        if (!rayBtn?.classList.contains('oip-btn--armed')) return;
        if (popup?.contains(e.target) || e.target === rayBtn || rayBtn.contains(e.target)) return;
        oipRSChart?.setRayMode(false);
        rayBtn.classList.remove('oip-btn--armed');
        popup?.classList.add('hidden');
    });
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            oipRSChart?.clearRays();
            oipRSClearSavedRays();
        });
    }
}

function oipRSInitIndicatorsPopup() {
    const btn = document.getElementById('oipRSIndicatorsBtn');
    const popup = document.getElementById('oipRSIndicatorsPopup');
    if (btn && popup) {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            popup.classList.toggle('hidden');
        });
        document.addEventListener('click', e => {
            if (!popup.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                popup.classList.add('hidden');
            }
        });
    }

    OIP_RS_LEG_KEYS.forEach(key => {
        document.getElementById(OIP_RS_LEG_SPECS[key].checkbox)?.addEventListener('change', () => {
            oipRSSyncLegVisibility();
            oipRSSaveIndicatorState();
        });
    });

    OIP_RS_DECIDER_KEYS.forEach(key => {
        document.getElementById(OIP_RS_DECIDER_CHECKBOX_IDS[key])?.addEventListener('change', () => {
            oipRSSyncDeciderVisibility();
            oipRSSaveIndicatorState();
        });
    });

    [OIP_RS_CE_STYLE_IDS.color, OIP_RS_CE_STYLE_IDS.width, OIP_RS_CE_STYLE_IDS.style].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener(el.type === 'color' ? 'input' : 'change', oipRSOnCeStyleChange);
    });
    [OIP_RS_PE_STYLE_IDS.color, OIP_RS_PE_STYLE_IDS.width, OIP_RS_PE_STYLE_IDS.style].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener(el.type === 'color' ? 'input' : 'change', oipRSOnPeStyleChange);
    });
    [OIP_RS_DEC_STYLE_IDS.color, OIP_RS_DEC_STYLE_IDS.width, OIP_RS_DEC_STYLE_IDS.style].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener(el.type === 'color' ? 'input' : 'change', oipRSOnDeciderStyleChange);
    });
    // 5m Close Border — toggle + colour both re-tag the loaded candles in place.
    document.getElementById('oipRSShow5mClose')?.addEventListener('change', oipRSOn5mCloseChange);
    document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.addEventListener('input', oipRSOn5mCloseChange);

    // Chg in OI — the toggles build or tear down the pane itself (not just a
    // series' visibility), the colour swatches repaint the parked bars.
    ['ce', 'pe'].forEach(side => {
        document.getElementById(OIP_RS_OI_CHG_SPECS[side].checkbox)
            ?.addEventListener('change', oipRSOnOiChgToggle);
        const inp = document.getElementById(OIP_RS_OI_CHG_STYLE_IDS[side]);
        if (!inp) return;
        // The swatch sits INSIDE the checkbox's own <label>, so the click has to
        // stop here or it reaches the label and flips the indicator off every
        // time the user opens the colour picker. Same guard _oipWireColorInput
        // applies to the shared swatches.
        inp.addEventListener('click', e => e.stopPropagation());
        inp.addEventListener('input', oipRSOnOiChgColorChange);
    });
}

// ── Data layer — /api/oi-profile/round-strike ────────────────────────────────
// One request per tick carries this block's whole picture: CE/PE candles, both
// volume overlays and the stats strip. Strikes are optional on it — the very
// first call goes out without them precisely to LEARN which pair to ask for
// (session_open + strikes), and the option legs come back empty until it does.

// The selected SETTLED expiry, or '' when there is none. Only Replay carries the
// dropdown, so this is always '' on the OI Profile page — which is exactly what
// keeps that page on its original live path.
function oipRSSelectedExpiry() {
    return document.getElementById('oipRSExpiryDropdown')?.value || '';
}

// The page symbol, on both pages. Replay used to give this block a dropdown of
// its own, which meant the two stacked charts could sit on different instruments
// without anything on screen saying so; there is one selector now, in the Replay
// toolbar, and oi_replay.js calls oipRSOnDateChanged when it moves.
function oipRSSymbol() {
    return oipSymbol;
}

// The as-of date: where the user is standing. Owned by the Replay page's own
// toolbar rather than this block, so a single date drives both the index chart
// above and the option legs here. Absent on the OI Profile page, which stays live.
function oipRSAsOfDate() {
    return document.getElementById('oipReplayDate')?.value || '';
}

// True on Replay, where this block is historical-only: it shows a settled
// contract, so there is nothing to poll for and no live tick to chase.
function oipRSIsHistorical() {
    return !!document.getElementById('oipRSExpiryDropdown');
}

function oipRSApiUrl(withStrikes = true) {
    const _daysForInterval = { day: 365, week: 1095, month: 3650 };
    // 10 trading days back from the as-of date on the historical block, matching
    // the window the index chart above it loads. The live page keeps its 5 — a
    // wider intraday window there is bars nobody scrolls back to, and at
    // 1-minute each extra pair of days is another rate-limited Breeze chunk.
    const days = _daysForInterval[oipRSInterval] ?? (oipRSIsHistorical() ? 10 : 5);
    const step = (typeof oipStrikeStep !== 'undefined' && oipStrikeStep) || 50;
    let url = `/api/oi-profile/round-strike?symbol=${oipRSSymbol()}&interval=${oipRSInterval}&days=${days}&step=${step}`;
    if (withStrikes) {
        const ce = document.getElementById('oipRSCEStrikeDropdown')?.value;
        const pe = document.getElementById('oipRSPEStrikeDropdown')?.value;
        if (ce && pe) url += `&ce_strike=${ce}&pe_strike=${pe}`;
    }
    const expiry = oipRSSelectedExpiry();
    const asOf = oipRSAsOfDate();
    if (expiry) url += `&expiry=${expiry}`;
    if (asOf) url += `&date=${asOf}`;
    // Tell the server which volume overlays are actually switched on. Each one
    // it can skip is five fewer rate-limited Breeze chunks at 1-minute bars,
    // and Banknifty Vol Fut is off by default — it was being fetched and
    // discarded on every request. Only sent from the historical block; the live
    // page omits them and keeps its original behaviour.
    if (oipRSIsHistorical()) {
        if (!(document.getElementById('oipRSShowVolume')?.checked ?? true)) url += '&vol=0';
        if (!(document.getElementById('oipRSShowBnfVolume')?.checked ?? false)) url += '&bnf_vol=0';
        // Replay has no stats strip to fill (it was removed), so everything
        // behind those pills — and the intraday index leg they need — is work
        // whose result this page throws away.
        url += '&hdr=0';
    }
    // The cache-buster is what keeps the live block at 1s. A historical window
    // is fixed by its own parameters, so it is left off and the browser cache
    // is allowed to answer.
    return oipRSIsHistorical() ? url : `${url}&_t=${Date.now()}`;
}

// Fills Replay's expiry dropdown and selects the most recent settled expiry.
// Silent no-op on the OI Profile page, which has no such element.
//
// Returns true once an expiry is selected. The caller waits on that before its
// first fetch: with no "Live" option there is nothing sensible to request until
// the list has arrived.
async function oipRSPopulateExpiries() {
    const sel = document.getElementById('oipRSExpiryDropdown');
    if (!sel) return false;
    try {
        const res = await fetch(`/api/oi-profile/expiries?symbol=${oipRSSymbol()}`
            + `&date=${oipRSAsOfDate()}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'request failed');
        sel.innerHTML = '';
        (data.expiries || []).forEach(iso => {
            const opt = document.createElement('option');
            opt.value = iso;
            // "08 Sep 26" — the year earns its place here in a way it never does
            // on a live expiry, since the date can be sent back over a year.
            const d = new Date(iso + 'T00:00:00');
            opt.textContent = isNaN(d) ? iso : d.toLocaleDateString('en-GB',
                { day: '2-digit', month: 'short', year: '2-digit' });
            sel.appendChild(opt);
        });
        if (!sel.options.length) throw new Error('no expiries open on that date');
        // The FRONT expiry, not the previous pick: having moved the date, the
        // contract that was current then is the one being asked about. Standing
        // on 04 Sep that is 08 Sep.
        sel.value = data.selected || sel.options[0].value;
        sel.title = 'Expiry open on the chosen date — the front one is picked';
        return true;
    } catch (e) {
        console.warn('[RoundStrike] expiry list failed:', e);
        // Say so in the control itself. An empty dropdown with no explanation
        // reads as a broken page; this reads as a missing ICICI session, which
        // is what it usually is.
        sel.innerHTML = '<option value="">unavailable</option>';
        sel.title = `Settled expiries unavailable: ${e.message}`;
        oipRSSetStaleChip(`Expiry list unavailable — ${e.message}`);
        return false;
    }
}

// First call — no strikes yet. Returns the day's OPEN (never a live/current
// price, so the round-strike default is stable through the session) plus the
// tradable strike list for the two dropdowns.
async function oipRSFetchOpenAndStrikes() {
    try {
        const res = await fetch(oipRSApiUrl(false));
        const data = await res.json();
        if (!data.success) return { openPrice: 0, strikes: [] };

        // The stats strip can already be painted from this first response —
        // no reason to leave it on '--' until the strikes resolve.
        oipRSApplyHeader(data.header);

        // strikes come back as {strike: number} objects — extract, dedupe, sort
        // (same shape/handling as Opt Prem's own dropdown population).
        const strikes = [...new Set((data.strikes || []).map(s => parseFloat(s.strike)))]
            .filter(n => !isNaN(n))
            .sort((a, b) => a - b);

        return { openPrice: Number(data.session_open) || 0, strikes };
    } catch (e) {
        console.warn('[RoundStrike] open-price fetch error:', e);
        return { openPrice: 0, strikes: [] };
    }
}

function oipRSPopulateDropdown(sel, strikes, selected) {
    if (!sel) return;
    sel.innerHTML = '';
    // If the computed target isn't an exact match (e.g. openPrice was
    // unavailable and near50 landed off the real chain), snap to the
    // nearest real strike instead of leaving the <select> to silently
    // default to whichever option happens to be first in the list.
    let snapTarget = Number(selected);
    if (strikes.length && !strikes.some(s => Number(s) === snapTarget)) {
        snapTarget = strikes.reduce((nearest, s) =>
            Math.abs(s - selected) < Math.abs(nearest - selected) ? s : nearest, strikes[0]);
    }
    strikes.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        if (Number(s) === snapTarget) opt.selected = true;
        sel.appendChild(opt);
    });
}

// Points the block at whatever symbol/expiry the dropdowns now say: re-reads the
// strike ladder and session open for that contract, re-derives the round-strike
// pair from it, and asks for a redraw.
//
// The parked candles are dropped first. They exist as anti-flicker cover for a
// leg the broker just refused, and reusing them across a contract change would
// paint one contract's premiums under another's labels.
async function oipRSLoadContract() {
    oipRSLastCeData = oipRSLastPeData = null;
    oipRSLastFutVol = oipRSLastBnfVol = null;
    oipRSCurrentCEStrike = oipRSCurrentPEStrike = null;

    const { openPrice, strikes } = await oipRSFetchOpenAndStrikes();
    const { ceStrike, peStrike } = oipRSComputeStrikes(openPrice);
    oipRSPopulateDropdown(document.getElementById('oipRSCEStrikeDropdown'), strikes, ceStrike);
    oipRSPopulateDropdown(document.getElementById('oipRSPEStrikeDropdown'), strikes, peStrike);

    oipRSPendingResetZoom = true;   // a settled window sits nowhere near the live one
    oipRSScheduleLoop(0);
}

// Replay position, as a bar time. The Round Strike chart draws nothing after it,
// so scrubbing the slider above walks BOTH charts forward together instead of
// leaving this one showing the whole session while the index chart replays.
// null = show everything (the OI Profile page never sets it).
let oipRSReplayCutoff = null;

function oipRSTrimToCutoff(rows) {
    if (oipRSReplayCutoff == null || !rows?.length) return rows || [];
    return rows.filter(r => r.time <= oipRSReplayCutoff);
}

// Called by oi_replay.js on every replay step. Redraws from the parked candles
// rather than refetching — the data is already here, only the cut moves.
window.oipRSApplyReplayCutoff = function (timeSec) {
    const next = (timeSec == null || !Number.isFinite(timeSec)) ? null : timeSec;
    if (next === oipRSReplayCutoff) return;
    oipRSReplayCutoff = next;
    if (!oipRSChart || !oipRSLastCeData) return;
    window._oipDataRefreshing = true;
    oipRSChart.update(
        oipRSMark5mCloseBorders(oipRSTrimToCutoff(oipRSLastCeData)),
        oipRSMark5mCloseBorders(oipRSTrimToCutoff(oipRSLastPeData)),
        false);
    requestAnimationFrame(() => { window._oipDataRefreshing = false; });
};

// Set by the callers that need the NEXT render to re-fit the chart (initial
// load, strike change). The render itself is driven by this block's own poll
// loop, which has no idea a strike just changed, so the intent is parked here
// and consumed by the first render that follows.
let oipRSPendingResetZoom = false;
let oipRSFirstRenderDone = false;
// Last rendered candles, kept UNTAGGED (no 5m-close borderColor) so indicator
// changes can re-tag and redraw them without a refetch — see oipRSOn5mCloseChange.
let oipRSLastCeData = null, oipRSLastPeData = null;
// Same idea for the two volume overlays. The candles had this guard from the
// start; the volume bars did not, so a rate-limited future leg arrived as [] and
// oipSetVolumeBars setData([])'d the whole row to nothing for that tick — the
// empty volume the block was reported for. Parked per interval, since bars from
// a different timeframe sit on a different time grid and must not be reused.
let oipRSLastFutVol = null, oipRSLastBnfVol = null, oipRSLastVolInterval = null;

// ── 5m Close Border indicator ────────────────────────────────────────────────
// This block's own instance of the marker shared with the main OI Profile and
// Opt Prem charts — see oipMark5mCloseBorders in oi_indicators.js for what it
// does and why. Two things differ: this block keeps its own toggle/colour
// pickers (the OIP_RS_* convention used throughout this file) rather than the
// generic oipGetLineColor store the two popups above use, and it marks against
// its OWN timeframe — which bar closes a 5-minute block depends on the bar
// width, and oipRSInterval is independent of the page's oipInterval.
//
// There is deliberately NO width or style picker here (unlike the CE/PE Line
// Style sections): lightweight-charts' candlestick renderer only accepts a
// border COLOUR — the outline is always a 1px solid hairline, so a width/style
// control would be a dead knob.
const OIP_RS_5M_CLOSE_COLOR_ID = 'oipRS5mCloseColorInp';
const OIP_RS_5M_CLOSE_DEFAULT = '#fbbf24';  // amber — reads against green/red (CE) and violet/grey (PE)

function oipRSMark5mCloseBorders(candles) {
    return oipMark5mCloseBorders(
        candles,
        document.getElementById('oipRSShow5mClose')?.checked ?? true,
        document.getElementById(OIP_RS_5M_CLOSE_COLOR_ID)?.value || OIP_RS_5M_CLOSE_DEFAULT,
        oipRSInterval
    );
}

// Re-applies the marker to the ALREADY-loaded candles so toggling the checkbox
// or dragging the colour picker takes effect immediately, instead of waiting
// for the next tick of this block's poll loop. Re-uses the raw (untagged)
// arrays parked by the last render — no refetch. `refresh=false` so the user's
// current pan/zoom survives.
let oipRS5mCloseRedrawPending = false;

function oipRSOn5mCloseChange() {
    oipRSSaveIndicatorState();
    oipRSSaveLineStyleState();
    oipRSUpdateCheckboxSpanColors();
    if (!oipRSChart || !oipRSLastCeData || oipRS5mCloseRedrawPending) return;
    // Coalesced to one redraw per frame — a colour <input> fires continuously
    // while the picker is dragged, and each redraw is a full setData of both legs.
    oipRS5mCloseRedrawPending = true;
    requestAnimationFrame(() => {
        oipRS5mCloseRedrawPending = false;
        window._oipDataRefreshing = true;
        oipRSChart.update(oipRSMark5mCloseBorders(oipRSLastCeData), oipRSMark5mCloseBorders(oipRSLastPeData), false);
        requestAnimationFrame(() => { window._oipDataRefreshing = false; });
    });
}

// Renders this block's chart from its own /api/oi-profile/round-strike
// response (see oipRSLoadData). The stats strip is painted separately by
// oipRSApplyHeader from the `header` object on that same response.
function oipRSRenderChart(data) {
    if (!data || !data.success) return;
    const ceStrike = document.getElementById('oipRSCEStrikeDropdown')?.value;
    const peStrike = document.getElementById('oipRSPEStrikeDropdown')?.value;
    if (!ceStrike || !peStrike) return;
    // A response that raced ahead of a strike change carries the OLD contract's
    // candles — charting them under the new labels would show the wrong strike
    // for a tick. Drop it; the request for the new pair is already in flight.
    if (String(data.ce_strike) !== String(ceStrike) || String(data.pe_strike) !== String(peStrike)) return;
    // Same race, one field over: an in-flight live response landing just after
    // the user picked a past expiry would draw today's premiums as that
    // expiry's. The backend echoes what it actually fetched, so compare.
    if (String(data.expiry || '') !== oipRSSelectedExpiry()) return;

    const resetZoom = oipRSPendingResetZoom;
    oipRSPendingResetZoom = false;

    // A leg whose Fyers history call was rate-limited into failure comes back as
    // [] (the adapter returns an empty list rather than raising), which would
    // otherwise setData() the chart to blank on that poll and repaint it on the
    // next one — the "candles disappear" flicker. Keep the last good candles
    // instead, but ONLY while the strike is unchanged: after a strike switch the
    // parked data belongs to a different contract and must not be shown.
    const sameStrikes = (ceStrike === oipRSCurrentCEStrike && peStrike === oipRSCurrentPEStrike);

    oipRSCurrentCEStrike = ceStrike;
    oipRSCurrentPEStrike = peStrike;

    let ceData = (data.ce_candles || []).map(c => ({ ...c, type: 'CE' }));
    let peData = (data.pe_candles || []).map(c => ({ ...c, type: 'PE' }));
    // Drop the pre-open and closing call-auction bars, on this block's OWN
    // timeframe — the index chart above already sheds them, and a Round Strike
    // chart still carrying them reads as a different session from the one it is
    // stacked under. Replay only: the live OI Profile view is left as it was.
    // Filtered here rather than on the way to the chart so the parked
    // oipRSLast*Data arrays the replay slider reads back are trimmed too.
    if (window.oipReplayMode && typeof oipStripAuctionBars === 'function') {
        ceData = oipStripAuctionBars(ceData, oipRSInterval);
        peData = oipStripAuctionBars(peData, oipRSInterval);
    }
    if (sameStrikes) {
        if (!ceData.length && oipRSLastCeData?.length) ceData = oipRSLastCeData;
        if (!peData.length && oipRSLastPeData?.length) peData = oipRSLastPeData;
    }
    // Parked untagged so the 5m Close Border indicator can be re-applied on a
    // checkbox/colour change without waiting for the next poll (oipRSOn5mCloseChange).
    oipRSLastCeData = ceData;
    oipRSLastPeData = peData;

    // Suppress the cross-chart sync listener (see oipRSInitCharts) while this
    // setData call is in flight — same guard oi_profile.js uses for OI/Opt Prem,
    // so a data refresh can't be mistaken for a user-driven pan/zoom.
    window._oipDataRefreshing = true;
    // Trimmed on the way to the chart, not in the parked arrays: oipRSLastCeData
    // stays whole so moving the replay slider back forward has data to show.
    if (oipRSChart) oipRSChart.update(oipRSMark5mCloseBorders(oipRSTrimToCutoff(ceData)),
                                      oipRSMark5mCloseBorders(oipRSTrimToCutoff(peData)), resetZoom);

    // Same anti-flicker rule as the candles above: an empty array means the
    // future leg failed this tick, not that volume went to nothing. Hold the
    // last good bars — but only while the timeframe is unchanged, since bars
    // from another interval sit on a different time grid and oipSetVolumeBars
    // matches them to the candles by exact time key.
    const sameVolInterval = (oipRSLastVolInterval === oipRSInterval);
    let futVol = data.future_volume || [];
    let bnfVol = data.banknifty_volume || [];
    if (sameVolInterval) {
        if (!futVol.length && oipRSLastFutVol?.length) futVol = oipRSLastFutVol;
        if (!bnfVol.length && oipRSLastBnfVol?.length) bnfVol = oipRSLastBnfVol;
    }
    oipRSLastFutVol = futVol;
    oipRSLastBnfVol = bnfVol;
    oipRSLastVolInterval = oipRSInterval;

    // Both overlays shade by size (the `intensity` flag): a bar well above the
    // recent median paints near solid, a quiet one fades back. The two bands
    // are only 20% of the pane tall and each autoscales on its own, so height
    // alone made a heavy bar hard to spot — and impossible to compare across
    // the two bands. Opt-in per call, so the main OI Profile charts keep their
    // flat 50% alpha.
    oipSetVolumeBars(oipRSVolumeSeries, futVol, ceData, 'nifty', true);
    // Banknifty deliberately uses the NIFTY colour pair here. Everywhere else the
    // two histograms share one scale and overlap, so Banknifty needs its own
    // colours to stay distinguishable; on this chart it hangs from its own top
    // band (bnfOnTop), so the same up/down pair reads consistently across both
    // bands instead of introducing a second colour language. The Banknifty
    // swatches are omitted from this block's Indicator popup for that reason.
    oipSetVolumeBars(oipRSBnfVolumeSeries, bnfVol, ceData, 'nifty', true);
    const volLegendEl = document.getElementById('oipRSVolLegendItem');
    if (volLegendEl) volLegendEl.classList.toggle('hidden', !data.future_symbol);
    const volSymbolEl = document.getElementById('oipRSLegendVolSymbol');
    if (volSymbolEl) volSymbolEl.textContent = data.future_symbol || '--';

    if (typeof oipCalculateVWAP === 'function') {
        if (oipRSVwapCESeries) oipRSVwapCESeries.setData(oipCalculateVWAP(ceData));
        if (oipRSVwapPESeries) oipRSVwapPESeries.setData(oipCalculateVWAP(peData));
    }

    // Chg in OI reads the `oi` field on these same candles, so it rides the
    // anti-flicker parking above for free — a rate-limited leg keeps its last
    // good histogram rather than blanking for a tick.
    oipRSUpdateOiChangeSeries(ceData, peData);
    oipRSSetOiChgTitles(ceStrike, peStrike);

    // Step series. Each leg's five levels come from its own candles; the
    // Deciders blend the two, so they're built once here from both.
    const legLevels = { Ce: oipRSComputeLegSeries(ceData), Pe: oipRSComputeLegSeries(peData) };
    OIP_RS_LEG_KEYS.forEach(key => {
        const spec = OIP_RS_LEG_SPECS[key];
        if (oipRSLegSeries[key]) oipRSLegSeries[key].setData(legLevels[spec.side][spec.field]);
    });

    const deciders = oipRSComputeDeciderSeries(ceData, peData);
    OIP_RS_DECIDER_KEYS.forEach(key => {
        if (oipRSDeciderSeries[key]) oipRSDeciderSeries[key].setData(deciders[key]);
    });

    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    setText('oipRSLegendCombinedCE', `${ceStrike} CE`);
    setText('oipRSLegendCombinedPE', `${peStrike} PE`);

    requestAnimationFrame(() => { window._oipDataRefreshing = false; });

    // Rays are restored once real data exists, so they extend to it correctly.
    if (!oipRSFirstRenderDone) {
        oipRSFirstRenderDone = true;
        oipRSRestoreSavedRays();
    }
}

// ── Stats strip ──────────────────────────────────────────────────────────────
// Every pill above the chart, straight off the `header` object in this block's
// own response. oi_profile.js used to write these from /api/open-interest on a
// 30-second loop; it now stands down (window._oipRSOwnsHeader) so the two can't
// fight over the same nodes with data of different ages.
//
// A missing value LEAVES THE PILL AS IT IS rather than blanking it: the OI
// snapshot behind half of these is written about once a minute, so on the ticks
// in between there is genuinely nothing new to say — and a number that flickers
// to '--' 20 times a minute is worse than one that simply holds.
function oipRSApplyHeader(h) {
    if (!h) return;
    const setVal = (id, text, cls) => {
        const el = document.getElementById(id);
        if (!el || text == null) return;
        el.textContent = text;
        if (cls !== undefined) el.className = 'oip-hdr-val' + (cls ? ' ' + cls : '');
    };
    const fmtOI = (typeof fmtL === 'function') ? fmtL : (v => v == null ? '--' : String(v));

    if (h.price) setVal('hdrPrice', Number(h.price).toLocaleString('en-IN', { minimumFractionDigits: 2 }));
    if (h.ce_oi != null) setVal('hdrCeOI', fmtOI(h.ce_oi), 'red');
    if (h.pe_oi != null) setVal('hdrPeOI', fmtOI(h.pe_oi), 'grn');
    if (h.atm != null) setVal('hdrAtm', h.atm);
    if (h.lot_size) setVal('hdrLotSize', h.lot_size);
    if (h.trend) setVal('hdrTrend', h.trend, h.trend === 'Bullish' ? 'grn' : (h.trend === 'Bearish' ? 'red' : ''));
    if (h.vwap_bias) setVal('hdrVwapBias', h.vwap_bias, h.vwap_bias === 'DOWN' ? 'red' : 'grn');
    if (h.atm_ce_oi_bias) setVal('hdrAtmCeOiBias', h.atm_ce_oi_bias, h.atm_ce_oi_bias === 'DOWN' ? 'red' : 'grn');

    // PCR carries its own card background at the extremes (the same >=1.7 /
    // <=0.7 thresholds the header has always used), so it needs more than setVal.
    if (h.pcr != null) {
        const pcr = Number(h.pcr);
        setVal('hdrPcr', pcr.toFixed(2));
        const card = document.getElementById('hdrPcrCard');
        if (card) {
            const dark = pcr >= 1.7 || pcr <= 0.7;
            card.style.background = pcr >= 1.7 ? '#7f1d1d' : (pcr <= 0.7 ? '#14532d' : '');
            card.querySelectorAll('.oip-hdr-lbl, .oip-hdr-val')
                .forEach(el => { el.style.color = dark ? '#ffffff' : ''; });
        }
    }

    if (typeof h.iv_percentile === 'number') {
        setVal('hdrIVP', h.iv_percentile.toFixed(1) + '%');
        if (typeof oipUpdateIVPGauge === 'function') oipUpdateIVPGauge(h.iv_percentile);
    }
    document.getElementById('ivCrushAlert')?.classList.toggle('hidden', !h.iv_crush_alert);

    // CPR keeps oi_profile_shared.js's renderer: the card's day toggle lives
    // there and reads the same oipCprData, so feeding it here keeps one code
    // path for both the click and the poll.
    if (h.cpr && (h.cpr.index || h.cpr.index_next) && typeof oipRenderCprCard === 'function') {
        oipCprData = h.cpr;
        oipRenderCprCard();
    }
}

// ── Poll loop ────────────────────────────────────────────────────────────────
// 1 second while the market is open, 5 minutes when it is closed (nothing
// moves), and paused entirely while the tab is hidden — a background tab
// polling a broker API 60 times a minute earns nothing but rate-limit pressure.
// Self-rescheduling rather than setInterval, so a slow tick can never stack a
// second request on top of the one still in flight: if a tick takes longer than
// the interval, the next one simply starts when it lands.
//
// This is the ONLY live feed on the page. Every other chart here is static
// until the Refresh All button is pressed (see oipFullRefresh in oi_profile.js),
// so the whole page's broker traffic is this loop.
const OIP_RS_POLL_MS = 1000;
const OIP_RS_POLL_MS_CLOSED = 300000;
const OIP_RS_POLL_MS_HIDDEN = 10000;   // re-check for tab visibility, no request
const OIP_RS_POLL_MS_ERROR = 3000;     // back off on failure rather than pile on
let oipRSPollTimer = null;
let oipRSIsLoading = false;
// Set when a reload is asked for while one is already in flight (a strike or TF
// change lands mid-request): that response is about to be discarded as stale,
// so the loop goes straight round again instead of waiting out a full tick.
let oipRSReloadPending = false;

// On the OI Profile page this drives the 1-second live feed. On Replay it does
// not run at all: that block shows a SETTLED expiry, whose candles are finished
// data, so re-requesting them on a timer would only burn Breeze's rate budget —
// much tighter than Fyers' — while the user scrubs the slider.
//
// A caller asking for an immediate refetch (delay 0) still gets one either way:
// that is a strike/TF/symbol/expiry change, not a poll.
function oipRSScheduleLoop(delay) {
    if (oipRSPollTimer) clearTimeout(oipRSPollTimer);
    if (oipRSIsHistorical() && delay > 0) return;
    oipRSPollTimer = setTimeout(() => {
        if (document.hidden) { oipRSScheduleLoop(OIP_RS_POLL_MS_HIDDEN); return; }
        oipRSPollTick();
    }, delay);
}

async function oipRSPollTick() {
    // Another tick is still waiting on the network — it schedules the next one
    // itself when it lands, so this one bows out rather than spinning. The flag
    // marks that response as already superseded (this tick was woken by a
    // strike/TF change), which sends the loop straight round again.
    if (oipRSIsLoading) { oipRSReloadPending = true; return; }

    const marketOpen = (typeof oipIsMarketOpen !== 'function') || oipIsMarketOpen();
    let ok = false;
    try {
        ok = await oipRSLoadData();
    } finally {
        if (oipRSReloadPending) {
            oipRSReloadPending = false;
            oipRSScheduleLoop(0);
        } else if (oipRSIsHistorical()) {
            // Historical: one fetch per change, no follow-up tick. oipRSScheduleLoop
            // would drop a delayed call anyway; not making it keeps the intent
            // visible here rather than buried in that guard.
            oipRSSetStaleChip(null);
        } else {
            oipRSScheduleLoop(!marketOpen ? OIP_RS_POLL_MS_CLOSED
                : (ok ? OIP_RS_POLL_MS : OIP_RS_POLL_MS_ERROR));
        }
    }
}

// Shows/hides the "delayed" chip beside the Live 1s badge.
//
// `reason` is the backend's data_stale/fetch_error string, or null when the tick
// was clean. A single throttled tick is routine and self-healing, so the chip
// only appears once the problem has survived a couple of polls — otherwise it
// would blink on and off all session and train the user to ignore it. Recovery
// clears it immediately: good news should never be delayed.
const OIP_RS_STALE_TICKS_BEFORE_WARN = 3;
let oipRSStaleStreak = 0;
function oipRSSetStaleChip(reason) {
    const el = document.getElementById('oipRSStaleChip');
    if (!el) return;
    if (!reason) {
        oipRSStaleStreak = 0;
        el.classList.add('hidden');
        el.title = '';
        return;
    }
    oipRSStaleStreak++;
    if (oipRSStaleStreak < OIP_RS_STALE_TICKS_BEFORE_WARN) return;
    el.textContent = 'Delayed';
    el.title = reason;          // full broker message on hover
    el.classList.remove('hidden');
}

// One request → chart + stats strip. Returns whether it succeeded, so the loop
// above can back off instead of hammering a broker that is already struggling.
async function oipRSLoadData() {
    if (oipRSIsLoading) {          // a tick is still in flight — not an error
        oipRSReloadPending = true;
        return true;
    }
    // Historical with no expiry means the list failed to load (usually a dead
    // ICICI session). Requesting anyway would answer with LIVE candles and draw
    // them in a block whose whole premise is a settled contract — worse than
    // drawing nothing, because nothing about the chart would say so.
    if (oipRSIsHistorical() && !oipRSSelectedExpiry()) return true;
    oipRSIsLoading = true;
    try {
        const res = await fetch(oipRSApiUrl(true));
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'request failed');
        if (data.fetch_error) console.warn('[RoundStrike]', data.fetch_error);
        oipRSApplyHeader(data.header);
        oipRSRenderChart(data);
        oipRSSetStaleChip(data.data_stale || data.fetch_error || null);
        // A tick the broker refused still parses fine, so it used to return true
        // and the loop went straight back round at 1s — piling more requests onto
        // a rate limit we were already over. Report it as a failure so the caller
        // picks OIP_RS_POLL_MS_ERROR and gives the broker room to recover.
        return !data.fetch_error;
    } catch (e) {
        console.warn('[RoundStrike] load error:', e);
        oipRSSetStaleChip('Connection problem — chart not updating');
        return false;
    } finally {
        oipRSIsLoading = false;
    }
}

function oipRSInitOrderButtons() {
    document.querySelectorAll('.oip-rs-order-btn').forEach(btn => {
        if (btn.id === 'oipRSExitAll') {
            btn.addEventListener('click', () => oipRSExitAllOrders(btn));
        } else if (btn.id === 'oipRSSLCE') {
            btn.addEventListener('click', () => oipRSPlaceSLOrders(btn, 'CE'));
        } else if (btn.id === 'oipRSSLPE') {
            btn.addEventListener('click', () => oipRSPlaceSLOrders(btn, 'PE'));
        } else {
            btn.addEventListener('click', () => oipRSPlaceOrder(btn.dataset.side, btn.dataset.action, btn));
        }
    });

    document.getElementById('oipRSLimitPrice')?.addEventListener('input', oipRSSyncSLButtons);
    // Also on init, not only on input. The field's value can arrive without an
    // input event ever firing — most often the browser restoring it across a
    // reload — and deriving the state solely from that event left both SL
    // buttons stuck disabled next to a perfectly valid trigger price.
    oipRSSyncSLButtons();
}

// SL buttons are enabled exactly when there is a usable trigger price. One rule,
// applied from every path that can change either input: typing, page init, and
// the end of a placement.
//
// A button mid-flight is skipped: it carries data-placing while its request is
// out, so a keystroke landing during the round trip cannot re-enable it and let
// the same SL be fired twice.
function oipRSSyncSLButtons() {
    const ok = parseFloat(document.getElementById('oipRSLimitPrice')?.value) > 0;
    ['oipRSSLCE', 'oipRSSLPE'].forEach(id => {
        const b = document.getElementById(id);
        if (b && !b.dataset.placing) b.disabled = !ok;
    });
}

/** Newest parked close for a leg — the stop-direction check's view of the market. */
function oipRSLastClose(side) {
    const rows = (side === 'CE') ? oipRSLastCeData : oipRSLastPeData;
    return rows?.length ? rows[rows.length - 1].close : null;
}

async function oipRSPlaceOrder(side, action, btn) {
    const strike = (side === 'CE') ? oipRSCurrentCEStrike : oipRSCurrentPEStrike;
    if (!strike) { showNotification(`No ${side} strike available.`, 'error'); return; }

    const mode = document.getElementById('oipRSOrderMode')?.value || 'broker';
    const rawLimit = parseFloat(document.getElementById('oipRSLimitPrice')?.value);
    const limitPrice = rawLimit && !isNaN(rawLimit) && rawLimit > 0 ? rawLimit : null;
    // From the dropdown, not inferred from the price box being filled — see the
    // same change in oipPlaceOrder (oi_profile.js).
    const orderType = document.getElementById('oipRSOrderType')?.value || 'MARKET';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    if (orderType !== 'MARKET' && !limitPrice) {
        showNotification(`Enter a ${orderType === 'STOP' ? 'trigger' : 'limit'} price first.`, 'error');
        return;
    }

    // Stop entries go through the shared helpers in oi_profile_shared.js, which
    // loads before this file — one endpoint, one direction check, one wording
    // for both toolbars.
    if (orderType === 'STOP') {
        if (mode === 'mine') {
            showNotification('STOP is broker-only — a Mine order cannot wait for a rise. Switch mode to Broker.', 'error');
            return;
        }
        const dirErr = oipStopDirectionError(action, limitPrice, oipRSLastClose(side));
        if (dirErr) { showNotification(dirErr, 'error'); return; }

        btn.disabled = true;
        const t = btn.title;
        btn.title = 'Placing...';
        try {
            const r = await oipPlaceStopOrder({ symbol: oipSymbol, strike, side, action, trigger: limitPrice });
            if (r.success) {
                showNotification(`Stop ${action} ${side} ${strike} resting at ₹${limitPrice} — triggers when the premium touches it.`, 'success');
            } else {
                showNotification(`Stop failed: ${oipStopErrorText(r)}`, 'error');
            }
        } catch (e) {
            showNotification(`Stop error: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.title = t;
        }
        return;
    }

    // MARKET means market — a leftover number in the price box must not turn it
    // into a limit order.
    const sendPrice = orderType === 'LIMIT' ? limitPrice : null;

    btn.disabled = true;
    const ot = btn.title;
    btn.title = 'Placing...';
    try {
        const endpoint = mode === 'mine' ? '/api/mine-orders' : '/api/orders/place';
        const body = {
            symbol: oipSymbol, strike: strike, option_type: side, action: action,
            strategy: 'intrinsic', order_type: orderType, limit_price: sendPrice
        };
        if (mode === 'mine') body.price = sendPrice || 0;

        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify(body)
        });
        const r = await res.json();
        if (r.success) {
            const modeLabel = mode === 'mine' ? 'Mine' : 'Broker';
            if (orderType === 'LIMIT') {
                showNotification(`${modeLabel}: Limit ₹${limitPrice} queued for ${side} ${strike}`, 'success');
            } else {
                showNotification(`${modeLabel}: ${action} ${side} ${strike} dispatched`, 'success');
            }
        } else {
            showNotification(`${r.error || 'Order failed'}`, 'error');
        }
    } catch (e) {
        showNotification(`Order error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.title = ot;
    }
}

async function oipRSPlaceSLOrders(btn, side) {
    const triggerPrice = parseFloat(document.getElementById('oipRSLimitPrice')?.value);
    if (!(triggerPrice > 0)) return;

    const strike = (side === 'CE') ? oipRSCurrentCEStrike : oipRSCurrentPEStrike;
    if (!strike) {
        showNotification('No CE/PE strike loaded — load data first.', 'error');
        return;
    }

    btn.disabled = true;
    btn.dataset.placing = '1';
    const origText = btn.innerText;
    btn.innerText = 'PLACING...';
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;

    try {
        const res = await fetch('/api/order/place-sl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({ symbol: oipSymbol, strike: strike, option_type: side, trigger_price: triggerPrice })
        });
        const r = await res.json();
        if (r.success) {
            showNotification(`SL placed for ${side} ${strike}.`, 'success');
        } else {
            // Prefixed with broker + instance: the common failures here are
            // per-account (an expired token on one login), and an unlabelled
            // list gives no clue which account to fix.
            const brokerErrors = (r.results || []).filter(b => !b.success)
                .map(b => `${b.broker || '?'}${b.instance ? ' ' + b.instance : ''}: ${b.error || b.message || 'Unknown error'}`);
            showNotification(`SL failed: ${brokerErrors.length ? brokerErrors.join(', ') : (r.error || 'Unknown error')}`, 'error');
        }
    } catch (e) {
        showNotification(`SL error: ${e.message}`, 'error');
    }

    btn.innerText = origText;
    delete btn.dataset.placing;
    // Back to whatever the trigger price says, rather than staying dead until
    // the price field is touched again. The old behaviour was a double-placement
    // guard, but a weak one — any keystroke re-enabled it, including retyping
    // the same number — and it left the button unusable after a placement that
    // had already reached the brokers, which is when you most want to arm the
    // other leg or re-arm this one.
    //
    // The real protection against a double fire is data-placing above: the
    // button cannot be clicked again while its own request is still out.
    oipRSSyncSLButtons();
}

async function oipRSExitAllOrders(btn) {
    // Double-click confirm: first click arms, second click within 3s executes.
    // NOTE: /api/order/exit-all is account-wide (no strike/strategy scoping),
    // same endpoint Opt Prem's own EXIT button calls — intentional, it's a
    // global panic-button reachable from either block.
    if (!btn._exitArmed) {
        btn._exitArmed = true;
        const prev = btn.innerText;
        btn.innerText = 'CONFIRM?';
        btn.style.background = '#7f1d1d';
        if (btn._exitArmTimer) clearTimeout(btn._exitArmTimer);
        btn._exitArmTimer = setTimeout(() => {
            btn._exitArmed = false;
            btn.innerText = prev;
            btn.style.background = '';
        }, 3000);
        return;
    }

    btn._exitArmed = false;
    if (btn._exitArmTimer) { clearTimeout(btn._exitArmTimer); btn._exitArmTimer = null; }

    btn.disabled = true;
    btn.innerText = 'EXITING...';
    btn.style.background = '';
    btn.style.opacity = '0.7';

    try {
        const res = await fetch('/api/order/exit-all', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content
            }
        });
        const r = await res.json();
        if (r.success) {
            const summary = (r.summary || []).map(s =>
                `${s.broker}_${s.instance}: ${s.cancelled_orders} Cancelled, ${s.exited_positions} Exited`
            ).join(' | ');
            showNotification(`Exit complete: ${summary || 'Done'}`, 'success');
        } else {
            showNotification(`Exit failed: ${r.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showNotification(`Exit error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerText = 'EXIT';
        btn.style.opacity = '1';
    }
}

// Refetches NOW instead of waiting out the rest of the current 1-second tick —
// used when the user changes something this block's request depends on (a
// strike, the TF dropdown). The next render re-fits the chart, since the bars
// it is about to draw belong to a different contract or timeframe.
function oipRSRequestReload() {
    oipRSPendingResetZoom = true;
    oipRSScheduleLoop(0);
}

async function oipRSInit() {
    if (!document.getElementById('oipRSCombinedChart')) return; // block not present on this page
    // Tells oi_profile.js to leave the stats strip alone — this block repaints
    // it from its own request every second (see oipRSApplyHeader).
    window._oipRSOwnsHeader = true;
    oipRSRestoreIndicatorState(); // before chart/series creation — VWAP's initial visibility reads the checkbox
    oipRSRestoreLineStyleState(); // before first load — CE/PE ref-line color/width/style pickers
    oipRSUpdateCheckboxSpanColors();

    // This block's own TF — read before the chart is built (its ray tool and the
    // 5m reference-line window both key off the interval) and independent of the
    // Opt Prem TF dropdown that drives every other chart on the page.
    const tfSel = document.getElementById('oipRSInterval');
    if (tfSel?.value) oipRSInterval = tfSel.value;
    tfSel?.addEventListener('change', e => {
        oipRSInterval = e.target.value;
        oipRSRequestReload();   // re-fit and re-request at the new bar width
    });

    oipRSInitCharts();

    // Replay: the expiry list has to land BEFORE the first fetch. There is no
    // "Live" fallback to request in the meantime, and the strike ladder and
    // session open both belong to the contract that is about to be chosen.
    // No-op (and no wait) on the OI Profile page.
    await oipRSPopulateExpiries();

    await oipRSLoadContract();

    // A strike change re-aims this block's own request at the new pair.
    const onStrikeChange = () => oipRSRequestReload();
    document.getElementById('oipRSCEStrikeDropdown')?.addEventListener('change', onStrikeChange);
    document.getElementById('oipRSPEStrikeDropdown')?.addEventListener('change', onStrikeChange);

    // Symbol and expiry both land on a different contract, so both go the long
    // way round — new strike ladder, new session open, new round-strike pair.
    // A symbol change invalidates the expiry list too: cadence differs per
    // symbol (NIFTY is weekly, BANKNIFTY monthly) and so do the dates.
    // Moving the date re-asks which expiries were open then and re-picks the
    // front one, so it goes through the same path as a symbol change.
    const onContractChange = async () => {
        await oipRSPopulateExpiries();
        await oipRSLoadContract();
    };
    // The date lives in the page toolbar now, so oi_replay.js calls this when it
    // moves rather than this block listening for it — the page has to re-window
    // its own chart on the same change, and one owner keeps the two in step.
    window.oipRSOnDateChanged = onContractChange;
    document.getElementById('oipRSExpiryDropdown')?.addEventListener('change', () => oipRSLoadContract());

    oipRSInitOrderButtons();
    oipRSInitRayTool();
    oipRSInitIndicatorsPopup();
    // Opens the Chg in OI pane if the restored checkboxes ask for it. Deliberately
    // NOT in oipRSInitCharts: the pane is created on demand so a user with the
    // indicator off never carries an empty one under the candles.
    oipRSSyncOiChgPane();

    // The first real tick was already requested by oipRSLoadContract above (the
    // bootstrap call before it went out without strikes, precisely to learn
    // them). Rays are restored by the render that follows — see oipRSRenderChart.

    // A tab that comes back to the foreground should show current data at once
    // rather than up to OIP_RS_POLL_MS_HIDDEN of staleness. Historical has no
    // staleness to catch up on, so it is left alone.
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && !oipRSIsHistorical()) oipRSScheduleLoop(0);
    });
}

document.addEventListener('DOMContentLoaded', () => { oipRSInit(); });
