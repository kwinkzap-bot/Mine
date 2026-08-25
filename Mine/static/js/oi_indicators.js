/**
 * oi_indicators.js
 * All indicator state, calculations, draw functions, and popup logic for OI Profile.
 * Loaded before oi_profile.js so global state is available during chart init.
 */

'use strict';

/* ── Indicator series state ───────────────────────────────── */
// EMA series — assigned by oipInitCharts in oi_profile.js, read here
let oipEma9Series = null, oipEma20Series = null, oipEma50Series = null,
    oipEma100Series = null, oipEma200Series = null;
let oipCEEma9Series = null, oipCEEma20Series = null, oipCEEma50Series = null;
let oipPEEma9Series = null, oipPEEma20Series = null, oipPEEma50Series = null;

// CPR, RSI, signal series + markers
let oipCprSeriesObj = null;
let oipCprSeriesMap = {};
let oipMultiCprSeriesMap = {};
let oipRSISeriesObj = null;
let oipSignalMarkers = [];
let oipRSIMarkers = [];

/* ── Indicator state persistence ─────────────────────────── */
const _OIP_IND_IDS = [
    'oipShowOIBars', 'oipShowVolume', 'oipShowBnfVolume', 'oipShowVwapInt', 'oipShowVwapGroup', 'oipShowCVWAP', 'oipShowPVWAP', 'oipShow3AvgVWAP',
    'oipShowCpr', 'oipCprShowPrevHL', 'oipCprShowBand', 'oipCprShowResistance', 'oipCprShowSupport', 'oipCprShowCumR3S3',
    'oipCprShowLabels',
    'oipShowSignals', 'oipShowRSI', 'oipShowAtmCeOi',
    'oipShowEma9', 'oipShowEma20', 'oipShowEma50', 'oipShowEma100', 'oipShowEma200',
    'oipShowMaxPain', 'oipShow2ndCandle30s', 'oipShow2nd5mCandle', 'oipShowMondayBox', 'oipShowPremium',
    'oipShow30mReversalLines', 'oipReversal30mCountUp', 'oipReversal30mCountDn', 'oipReversal30mRange',
    'oipShow1DReversalLines',  'oipReversal1DCount',  'oipReversal1DRange',
    'oipShowMultiCpr', 'oipMultiCpr15m', 'oipMultiCpr30m', 'oipMultiCpr1h',
    'oipShow5mClose', 'oipShowOpt5mClose',
    'oipShowSynthetic', 'oipShow2ndCandle30sOpt', 'oipShow2nd5mCandleOpt', 'oipShowVwapOpt', 'oipShowVolumeOpt', 'oipShowBnfVolumeOpt',
    'oipShowFixedCeAvg', 'oipShowFixedPeAvg', 'oipShowFixedCePeAvg',
    'oipShowEma9Opt', 'oipShowEma20Opt', 'oipShowEma50Opt'
];

function _oipSaveIndicators(key) {
    const state = {};
    _OIP_IND_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        state[id] = el.type === 'checkbox' ? el.checked : el.value;
    });
    try { localStorage.setItem(key, JSON.stringify(state)); } catch(e) {}
}

function _oipRestoreIndicators(key) {
    let state;
    try { state = JSON.parse(localStorage.getItem(key) || 'null'); } catch(e) {}
    if (!state) return;
    _OIP_IND_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el || !(id in state)) return;
        if (el.type === 'checkbox') el.checked = state[id];
        else el.value = state[id];
    });
}

/* ── Per-line style (Solid / Dotted / Dashed) ─────────────────────────────
   One dropdown per existing indicator checkbox, injected right into that
   checkbox's <label class="oip-ind-item">. LightweightCharts v5 LineStyle
   enum: 0=Solid, 1=Dotted, 2=Dashed (the only three exposed here). Persisted
   separately from _OIP_IND_IDS since it's a style choice, not a toggle. */
const _OIP_LINE_STYLE_STORAGE_KEY = 'oip-line-styles';
let oipLineStyles = {};

function _oipLoadLineStyles() {
    try { oipLineStyles = JSON.parse(localStorage.getItem(_OIP_LINE_STYLE_STORAGE_KEY) || '{}') || {}; }
    catch(e) { oipLineStyles = {}; }
}
function _oipSaveLineStyles() {
    try { localStorage.setItem(_OIP_LINE_STYLE_STORAGE_KEY, JSON.stringify(oipLineStyles)); } catch(e) {}
}
// Default Solid (0) for any key with no saved preference yet.
// Preserves each indicator's pre-existing hardcoded look (e.g. Max Pain and
// the 9:18 ATM CE OI lines were already dashed) until the user picks a style.
const _OIP_LINE_STYLE_DEFAULTS = { maxPain: 2, atmCeOi: 2, cprPrevHL: 2 };
function oipGetLineStyle(key) {
    if (key in oipLineStyles) return oipLineStyles[key];
    return _OIP_LINE_STYLE_DEFAULTS[key] ?? 0;
}

/* ── Per-line color + width — same idea, same storage pattern as style
   above. Each key's DEFAULT mirrors its current hardcoded look so nothing
   changes visually until the user picks a color/width. rsi and reversal1d
   are intentionally excluded: rsi has 4 differently-colored sub-lines and
   reversal1d's color is dynamic (green=bullish/red=bearish per candle) —
   a single override would erase the distinction those exist to show. */
const _OIP_LINE_COLOR_STORAGE_KEY = 'oip-line-colors';
const _OIP_LINE_WIDTH_STORAGE_KEY = 'oip-line-widths';
let oipLineColors = {};
let oipLineWidths = {};

const _OIP_LINE_DEFAULTS = {
    cvwap: { color: '#3b82f6', width: 2 }, pvwap: { color: '#fdba74', width: 2 }, avg3vwap: { color: '#ef4444', width: 2 },
    ceAvg: { color: '#16a34a', width: 1 }, peAvg: { color: '#7c3aed', width: 1 }, cepeAvg: { color: '#000000', width: 1 },
    cprPrevHL: { color: '#ef07f9', width: 1 }, cprBand: { color: '#3f51b5', width: 1 },
    cprResistance: { color: '#006400', width: 1 }, cprSupport: { color: '#ff0000', width: 1 }, cprCumR3S3: { color: '#7b1fa2', width: 2 },
    multiCpr15m: { color: '#f97316', width: 1 }, multiCpr30m: { color: '#06b6d4', width: 1 }, multiCpr1h: { color: '#9c28b0', width: 1 },
    ema9: { color: '#22c55e', width: 1 }, ema20: { color: '#f97316', width: 1 }, ema50: { color: '#ef4444', width: 1 },
    ema100: { color: '#3b82f6', width: 1 }, ema200: { color: '#888888', width: 1 },
    maxPain: { color: '#2563eb', width: 2 }, atmCeOi: { color: '#000000', width: 1 }, reversal30m: { color: '#f97316', width: 1 },
    synthDiff: { color: '#000000', width: 2 }, synthPdc: { color: '#22d3ee', width: 2 }, synthCp: { color: '#a78bfa', width: 2 },
    box30s: { color: '#FFC800', width: 1, opacity: 0.09 }, box5m: { color: '#2dd2ff', width: 1, opacity: 0.09 },
    mondayBox: { color: '#34ed0b', width: 1 },
    fixedCeAvg: { color: '#16a34a', width: 1 }, fixedPeAvg: { color: '#7c3aed', width: 1 }, fixedCePeAvg: { color: '#000000', width: 1 },
    fiveMClose: { color: '#fbbf24', width: 1 }, fiveMCloseOpt: { color: '#fbbf24', width: 1 },
    volUp: { color: '#1b9981' }, volDn: { color: '#f23645' },
    // The single colour every bar falls back to with Vol Direction Color off
    // (see the flag below) — a neutral grey, so a flat histogram reads as
    // size only and never as a direction the candles don't agree with.
    volFlat: { color: '#8a8f98' },
    // Banknifty's overlay defaults to the PE chart's candle colours (violet up,
    // dark down), which also keeps it clear of the green/red pair above — the
    // two histograms share a price scale and overlap. PE's down colour is
    // theme-dependent, so this default is a function: it re-resolves per read,
    // and a user-picked colour still overrides it outright.
    bnfVolUp: { color: '#8b5cf6' }, bnfVolDn: { color: () => _oipPeDownColor() },
    bnfVolFlat: { color: '#8a8f98' },
};

// The PE candle series' down colour — black on light themes, grey on dark.
// Mirrors the rule in tradingview-chart.js (both the initial `isLightTheme`
// branch and its 'themechanged' handler); keep the two in step.
const _OIP_LIGHT_THEMES = new Set(['light', 'cream', 'ocean']);
function _oipPeDownColor() {
    let theme = 'dark';
    try { theme = window.AppTheme?.getActiveTheme() || 'dark'; } catch (e) {}
    return _OIP_LIGHT_THEMES.has(theme) ? '#1f2937' : '#6b7280';
}
// Keys that only get a style dropdown (no color/width — see comment above).
const _OIP_NO_COLOR_KEYS = new Set(['rsi', 'reversal1d']);
// Keys that get ONLY a color input — no width, no style. The 5m Close Border
// isn't a line: it recolors a candlestick's border, and lightweight-charts'
// candle renderer accepts a border COLOUR only (always a 1px solid hairline),
// so width/style selects here would be dead knobs.
const _OIP_COLOR_ONLY_KEYS = new Set(['fiveMClose', 'fiveMCloseOpt']);
// Keys that also get a background fill-opacity control — only the box
// indicators have a filled background (a translucent rect between the box's
// high/low border lines); every other indicator here is a bare line.
const _OIP_FILL_OPACITY_KEYS = new Set(['box30s', 'box5m']);

function _oipLoadLineColorsWidths() {
    try { oipLineColors = JSON.parse(localStorage.getItem(_OIP_LINE_COLOR_STORAGE_KEY) || '{}') || {}; } catch(e) { oipLineColors = {}; }
    try { oipLineWidths = JSON.parse(localStorage.getItem(_OIP_LINE_WIDTH_STORAGE_KEY) || '{}') || {}; } catch(e) { oipLineWidths = {}; }
}
// One-off: drop a stale saved width/colour for the Camarilla R3/S3 pair so the
// 2px violet default below actually applies. Runs once per browser — after that
// the popup's own width/colour pickers own the value again, as for every other
// line. Delete this block (and the flag) once it has been out for a while.
const _OIP_CAMARILLA_RESET_FLAG = 'oip-camarilla-reset-v1';
function _oipResetCamarillaOverrides() {
    try {
        if (localStorage.getItem(_OIP_CAMARILLA_RESET_FLAG)) return;
        localStorage.setItem(_OIP_CAMARILLA_RESET_FLAG, '1');
        if (oipLineWidths.cprCumR3S3 != null) { delete oipLineWidths.cprCumR3S3; _oipSaveLineWidths(); }
        if (oipLineColors.cprCumR3S3 != null) { delete oipLineColors.cprCumR3S3; _oipSaveLineColors(); }
    } catch(e) {}
}

function _oipSaveLineColors() { try { localStorage.setItem(_OIP_LINE_COLOR_STORAGE_KEY, JSON.stringify(oipLineColors)); } catch(e) {} }
function _oipSaveLineWidths() { try { localStorage.setItem(_OIP_LINE_WIDTH_STORAGE_KEY, JSON.stringify(oipLineWidths)); } catch(e) {} }
// A default may be a plain hex or a function (re-resolved per read, for the
// theme-dependent ones — see bnfVolDn). A user-picked colour always wins.
function oipGetLineColor(key) {
    const saved = oipLineColors[key];
    if (saved != null) return saved;
    const def = _OIP_LINE_DEFAULTS[key]?.color;
    return (typeof def === 'function' ? def() : def) ?? '#000000';
}
function oipGetLineWidth(key) { return oipLineWidths[key] ?? _OIP_LINE_DEFAULTS[key]?.width ?? 1; }

// Background fill opacity (0-1) — box30s / box5m only, see _OIP_FILL_OPACITY_KEYS.
const _OIP_LINE_OPACITY_STORAGE_KEY = 'oip-line-opacities';
let oipLineOpacities = {};
function _oipLoadLineOpacities() {
    try { oipLineOpacities = JSON.parse(localStorage.getItem(_OIP_LINE_OPACITY_STORAGE_KEY) || '{}') || {}; }
    catch(e) { oipLineOpacities = {}; }
}
function _oipSaveLineOpacities() { try { localStorage.setItem(_OIP_LINE_OPACITY_STORAGE_KEY, JSON.stringify(oipLineOpacities)); } catch(e) {} }
function oipGetLineOpacity(key) { return oipLineOpacities[key] ?? _OIP_LINE_DEFAULTS[key]?.opacity ?? 0.09; }

/* ── Vol Fut histogram bars (Nifty + Banknifty) ───────────────────────────
   The index and the options carry no real traded volume of their own, so
   every volume histogram on the page (main OI chart, the four Opt Prem
   charts, both Round Strike charts) plots FUTURES volume, tinted by the
   direction of whatever candles that chart draws.

   Two independent overlays, each with its own up/down colour pair: 'nifty'
   (the selected symbol's own current-expiry future) and 'banknifty' (always
   BANKNIFTY's — `banknifty_volume` in the /api/oi-profile/candles response;
   when BANKNIFTY *is* the selected symbol the backend returns the same data
   for both). One colour pair drives that overlay on every chart, same "one
   control, every chart" pattern as the checkboxes that toggle them.

   Both share their chart's single volume price scale so bar heights stay
   directly comparable, and both paint at 50% alpha so candles — and the
   other overlay — stay readable through them. */
const _OIP_VOL_BAR_ALPHA = '80';
const _OIP_VOL_COLOR_KEYS = {
    nifty:     ['volUp', 'volDn', 'volFlat'],
    banknifty: ['bnfVolUp', 'bnfVolDn', 'bnfVolFlat'],
};
const _OIP_VOL_COLOR_KEY_SET = new Set(Object.values(_OIP_VOL_COLOR_KEYS).flat());
// The overlay's up/down/flat hues, WITHOUT an alpha suffix — the painter
// appends that, since it is per-bar once intensity shading is on.
function oipVolumeBarColors(kind) {
    const [upKey, dnKey, flatKey] = _OIP_VOL_COLOR_KEYS[kind] || _OIP_VOL_COLOR_KEYS.nifty;
    return { up: oipGetLineColor(upKey), down: oipGetLineColor(dnKey), flat: oipGetLineColor(flatKey) };
}

/* Vol Direction Color — ONE page-wide flag, mirrored as a checkbox in every
   Indicator popup, same "one control, every chart" rule the colour swatches
   above follow. On (the default, and how the bars have always looked) each bar
   takes the direction of the candle it sits under. Off, every bar on every
   chart paints its overlay's flat colour instead, so the histogram carries
   size only and direction is left to the candles.

   Intensity shading is a separate knob and is unaffected either way: it moves
   the ALPHA, never the hue, so a flat row still darkens on its spikes.

   Read straight from localStorage at load rather than in the popup's init,
   because charts paint their first bars before oipInitIndicatorsPopup runs —
   the ordering issue oipApplyAllLineStyles exists to paper over for lines. */
const _OIP_VOL_DIR_COLOR_STORAGE_KEY = 'oip-vol-dir-color';
let _oipVolDirColor = true;
function _oipLoadVolDirColor() {
    try { _oipVolDirColor = localStorage.getItem(_OIP_VOL_DIR_COLOR_STORAGE_KEY) !== '0'; }
    catch (e) { _oipVolDirColor = true; }
}
_oipLoadVolDirColor();
function oipVolDirColorOn() { return _oipVolDirColor; }

/* Volume-weighted shading — opt-in per call site via oipSetVolumeBars'
   `intensity`, on for the Round Strike block. Squeezed into a 20%-tall band,
   bar HEIGHT alone is a poor read of how big a bar is; with intensity on the
   alpha carries it too, so a spike paints near solid and a quiet bar fades
   back. Hue still comes from the candle direction, so the colour pickers keep
   working exactly as before — this only moves the transparency.

   The yardstick is the MEDIAN of the LOOKBACK bars BEFORE each bar, never the
   bar itself and never the whole series. Trailing, so appending the live bar
   can't re-tint the ones already drawn — a max-of-series scale would, on every
   new high. Median rather than average because the average is moved by the very
   spikes this is meant to pick out: one 6x bar lifts a 20-bar mean by a quarter
   and quietly fades the next twenty normal bars, where the median doesn't budge.

   The ramp starts ABOVE the median rather than straddling it. It used to run
   0.5x..2x median into alpha 0.22..0.95, which put a merely typical bar at
   ~0.46 — barely lighter than the flat 0.5 it replaced — and anything 1.5x at
   0.71. Nearly every bar therefore read as dark and the shading picked nothing
   out.

   Where the ramp sits is a taste call, tuned against a live chart — RATIO below
   is the only thing to move. At [4, 5] a bar must be four times its recent
   median before it darkens at all and hits solid at five, so the dark bars are
   the rare genuine spikes and everything else is deliberately flat.

   The ALPHA floor is a separate decision from where the ramp starts. It sits at
   0.24 rather than lower because that clamped mass is most of the row: at 0.14
   the quiet bars washed out almost to the pane background and the histogram
   stopped reading as one between spikes. The floor is the baseline the eye
   measures the spikes against, so it can be quiet but not absent. */
const _OIP_VOL_INTENSITY_LOOKBACK = 20;
const _OIP_VOL_INTENSITY_RATIO = [4.0, 5.0];  // x recent median: light end .. dark end
const _OIP_VOL_INTENSITY_ALPHA = [0.35, 0.95];

function _oipAlphaHex(a) {
    return Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
}

// Per-bar alpha suffixes for `bars`, same length and order. Bars with no
// history behind them yet (the first of the series, or an all-zero window) keep
// the flat alpha rather than guessing.
function _oipVolIntensityAlphas(bars) {
    const [rLo, rHi] = _OIP_VOL_INTENSITY_RATIO;
    const [aLo, aHi] = _OIP_VOL_INTENSITY_ALPHA;
    const vals = bars.map(b => Number(b.value) || 0);
    // A bar with nothing behind it yet gets the LIGHT end, not the flat 0.5 of
    // the non-intensity path: with no window there is no evidence it is big,
    // and defaulting dark made the first bars of every series look like spikes.
    const noRef = _oipAlphaHex(aLo);
    return vals.map((v, i) => {
        const win = vals.slice(Math.max(0, i - _OIP_VOL_INTENSITY_LOOKBACK), i);
        if (!win.length) return noRef;
        win.sort((a, b) => a - b);
        const mid = win.length >> 1;
        const ref = win.length % 2 ? win[mid] : (win[mid - 1] + win[mid]) / 2;
        if (!(ref > 0)) return noRef;
        const t = Math.max(0, Math.min(1, (v / ref - rLo) / (rHi - rLo)));
        return _oipAlphaHex(aLo + t * (aHi - aLo));
    });
}

// Creates one chart's pair of volume histograms. By default both sit on the
// SAME hidden price scale, pinned to the bottom 20% of the pane, so the two are
// comparable by bar height and neither competes with candle prices. Banknifty is
// added second so it draws on top of Nifty; at 50% alpha each, the overlap reads
// as a blend rather than one hiding the other. `chart` is the raw
// LightweightCharts object (i.e. `X.chart` for TradingViewChart wrappers).
//
// With `bnfOnTop`, Banknifty instead gets its OWN hidden scale pinned to the TOP
// 20% of the pane and hangs DOWNWARD from the top edge — a mirror image of the
// bottom volume, not a second upright histogram (Nifty stays exactly where it
// is at the bottom). lightweight-charts histograms always grow up from their
// base, so the flip is done by plotting NEGATIVE values (see
// _oipInvertedVolSeries / _oipPaintVolumeBars): autoscale then puts zero at the
// top of the band and the bars extend down into it. Each band autoscales on its
// own, so bar heights are comparable inside a band but not across the two.
// Returns [niftySeries, banknNiftySeries].
function oipAddVolumeSeriesPair(chart, priceScaleId, showNifty = true, showBnf = false, bnfOnTop = false) {
    const base = {
        priceFormat: { type: 'volume' },
        priceScaleId,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
    };
    const bnfScaleId = bnfOnTop ? `${priceScaleId}Top` : priceScaleId;
    const nifty = chart.addSeries(LightweightCharts.HistogramSeries, { ...base, visible: showNifty });
    const bnf   = chart.addSeries(LightweightCharts.HistogramSeries, { ...base, priceScaleId: bnfScaleId, visible: showBnf });
    chart.priceScale(priceScaleId).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
    if (bnfOnTop) {
        chart.priceScale(bnfScaleId).applyOptions({ scaleMargins: { top: 0, bottom: 0.8 }, visible: false });
        _oipInvertedVolSeries.add(bnf);
    }
    return [nifty, bnf];
}

// series -> {kind, bars:[{time, value, up}]} last pushed to it. Direction is
// baked into a histogram point's `color`, so without this cache a colour
// change couldn't tell an up bar from a down one after the fact — and
// refetching candles just to repaint would be wasteful. `kind` rides along so
// a repaint knows which colour pair the series belongs to.
const _oipVolBarCache = new Map();

// Series that hang downward from the top of the pane instead of standing up
// from the bottom — populated by oipAddVolumeSeriesPair's `bnfOnTop`. The flip
// is purely a plotting detail (negated values); everything upstream —
// oipSetVolumeBars' cache, the colour pickers, the checkboxes — keeps working
// with the real positive volumes.
const _oipInvertedVolSeries = new WeakSet();

function _oipPaintVolumeBars(series, kind, bars, intensity = false) {
    const { up, down, flat } = oipVolumeBarColors(kind);
    const byDirection = oipVolDirColorOn();
    const alphas = intensity ? _oipVolIntensityAlphas(bars) : null;
    const sign = _oipInvertedVolSeries.has(series) ? -1 : 1;
    try {
        series.setData(bars.map((b, i) => ({
            time: b.time,
            value: sign * b.value,
            color: (byDirection ? (b.up ? up : down) : flat) + (alphas ? alphas[i] : _OIP_VOL_BAR_ALPHA),
        })));
    } catch (e) {}
}

// Builds and pushes one chart's volume bars. `futureVolume` is the backend's
// array (future_volume / banknifty_volume / their fixed_* twins); `refCandles`
// are the candles actually drawn on that chart — they share the same
// interval-driven time-bucket grid, so matching by exact time key is safe.
// Bars are emitted only where refCandles has a REAL (non-whitespace) candle,
// so gaps don't render a misleading zero-volume bar. `kind` picks the colour
// pair — see _OIP_VOL_COLOR_KEYS. `intensity` shades each bar by its size
// against the recent median instead of one flat alpha — see
// _oipVolIntensityAlphas.
function oipSetVolumeBars(series, futureVolume, refCandles, kind = 'nifty', intensity = false) {
    if (!series) return;
    const futVolMap = new Map((futureVolume || []).map(v => [Number(v.time), Number(v.volume || 0)]));
    const bars = [];
    if (futVolMap.size) {
        (refCandles || []).forEach(c => {
            if (!c || c.open === undefined || c.close === undefined) return; // skip whitespace-only bars
            const t = Number(c.time);
            if (!futVolMap.has(t)) return;
            bars.push({ time: t, value: futVolMap.get(t), up: Number(c.close) >= Number(c.open) });
        });
    }
    _oipVolBarCache.set(series, { kind, bars, intensity });
    _oipPaintVolumeBars(series, kind, bars, intensity);
}

// Re-tints every already-drawn histogram in place — no refetch, no redraw of
// anything else. Called when any of the four volume colour pickers changes;
// each series repaints from its own cached `kind`, so only the overlay that
// actually changed ends up looking different.
function oipRepaintAllVolumeBars() {
    _oipVolBarCache.forEach(({ kind, bars, intensity }, series) => _oipPaintVolumeBars(series, kind, bars, intensity));
}

// The PE candle colour this overlay defaults to flips with the theme, so follow
// it: repaint the bars and refresh the swatches. No-op once the user has picked
// their own colour — an explicit choice shouldn't move when the theme does.
window.addEventListener('themechanged', () => {
    if (oipLineColors.bnfVolDn != null) return;
    const c = oipGetLineColor('bnfVolDn');
    document.querySelectorAll('.oip-line-color-inp[data-color-key="bnfVolDn"]')
        .forEach(inp => { inp.value = c; });
    oipRepaintAllVolumeBars();
});

// key -> checkbox id whose <label class="oip-ind-item"> gets the dropdown appended.
const _OIP_LINE_STYLE_ITEMS = [
    { key: 'cvwap',          checkboxId: 'oipShowCVWAP' },
    { key: 'pvwap',          checkboxId: 'oipShowPVWAP' },
    { key: 'avg3vwap',       checkboxId: 'oipShow3AvgVWAP' },
    { key: 'cprPrevHL',      checkboxId: 'oipCprShowPrevHL' },
    { key: 'cprBand',        checkboxId: 'oipCprShowBand' },
    { key: 'cprResistance',  checkboxId: 'oipCprShowResistance' },
    { key: 'cprSupport',     checkboxId: 'oipCprShowSupport' },
    { key: 'cprCumR3S3',     checkboxId: 'oipCprShowCumR3S3' },
    { key: 'multiCpr15m',    checkboxId: 'oipMultiCpr15m' },
    { key: 'multiCpr30m',    checkboxId: 'oipMultiCpr30m' },
    { key: 'multiCpr1h',     checkboxId: 'oipMultiCpr1h' },
    { key: 'ema9',           checkboxId: 'oipShowEma9' },
    { key: 'ema20',          checkboxId: 'oipShowEma20' },
    { key: 'ema50',          checkboxId: 'oipShowEma50' },
    { key: 'ema100',         checkboxId: 'oipShowEma100' },
    { key: 'ema200',         checkboxId: 'oipShowEma200' },
    { key: 'rsi',            checkboxId: 'oipShowRSI' },
    { key: 'maxPain',        checkboxId: 'oipShowMaxPain' },
    { key: 'atmCeOi',        checkboxId: 'oipShowAtmCeOi' },
    { key: 'reversal30m',    checkboxId: 'oipShow30mReversalLines' },
    { key: 'reversal1d',     checkboxId: 'oipShow1DReversalLines' },
    { key: 'box30s',         checkboxId: 'oipShow2ndCandle30s' },
    { key: 'box5m',          checkboxId: 'oipShow2nd5mCandle' },
    { key: 'mondayBox',      checkboxId: 'oipShowMondayBox' },
    { key: 'fiveMClose',     checkboxId: 'oipShow5mClose' },
    { key: 'fiveMCloseOpt',  checkboxId: 'oipShowOpt5mClose' },
];
// Synthetic value's 3 named lines share ONE checkbox — inject all 3 selects
// after it instead of the generic one-per-checkbox pattern above.
const _OIP_SYNTH_STYLE_ITEMS = [
    { key: 'synthDiff', label: 'Diff' },
    { key: 'synthPdc',  label: 'PDC' },
    { key: 'synthCp',   label: 'C-P' },
];

// Wires an ALREADY-IN-THE-DOM <select class="oip-line-style-sel" data-style-key="...">
// — restores its persisted value and attaches the change listener. Shared by
// both freshly-injected selects and hand-written ones (e.g. the Fixed Chart
// Lines rows, which have no checkbox to inject after).
function _oipWireStyleSelect(sel) {
    if (sel.dataset.wired) return;
    sel.dataset.wired = '1';
    const key = sel.dataset.styleKey;
    sel.value = String(oipGetLineStyle(key));
    // Prevent the click from also toggling the parent <label>'s checkbox.
    sel.addEventListener('click', e => e.stopPropagation());
    sel.addEventListener('change', e => {
        e.stopPropagation();
        oipLineStyles[key] = parseInt(sel.value, 10);
        _oipSaveLineStyles();
        oipApplyLineStyleChange(key);
    });
}

function _oipBuildStyleSelect(key) {
    const sel = document.createElement('select');
    sel.className = 'oip-line-style-sel';
    sel.dataset.styleKey = key;
    sel.title = 'Line style';
    [[0, 'Solid'], [1, 'Dotted'], [2, 'Dashed']].forEach(([val, label]) => {
        const opt = document.createElement('option');
        opt.value = val; opt.textContent = label;
        sel.appendChild(opt);
    });
    _oipWireStyleSelect(sel);
    return sel;
}

// Wires an ALREADY-IN-THE-DOM <input type="color" data-color-key="...">.
function _oipWireColorInput(inp) {
    if (inp.dataset.wired) return;
    inp.dataset.wired = '1';
    const key = inp.dataset.colorKey;
    inp.value = oipGetLineColor(key);
    inp.addEventListener('click', e => e.stopPropagation());
    inp.addEventListener('input', e => {
        e.stopPropagation();
        oipLineColors[key] = inp.value;
        _oipSaveLineColors();
        // A key can have more than one swatch on the page — the volume colours
        // are repeated in all three Indicator popups (see volUp/volDn). Keep
        // the other copies in step so they don't show a stale colour.
        document.querySelectorAll(`.oip-line-color-inp[data-color-key="${key}"]`)
            .forEach(other => { if (other !== inp) other.value = inp.value; });
        oipApplyLineStyleChange(key);
    });
}

// Wires an ALREADY-IN-THE-DOM <input type="checkbox" class="oip-vol-dircolor-cb">.
// Each Indicator popup carries one and they are all the SAME setting, so a
// change persists the flag, syncs the other copies and repaints every
// histogram from its cache — no refetch, nothing else on the chart touched.
function _oipWireVolDirColorCheckbox(cb) {
    if (cb.dataset.wired) return;
    cb.dataset.wired = '1';
    cb.checked = oipVolDirColorOn();
    cb.addEventListener('change', () => {
        _oipVolDirColor = cb.checked;
        try { localStorage.setItem(_OIP_VOL_DIR_COLOR_STORAGE_KEY, _oipVolDirColor ? '1' : '0'); } catch (e) {}
        document.querySelectorAll('.oip-vol-dircolor-cb')
            .forEach(other => { if (other !== cb) other.checked = _oipVolDirColor; });
        oipRepaintAllVolumeBars();
    });
}

function _oipBuildColorInput(key) {
    const inp = document.createElement('input');
    inp.type = 'color';
    inp.className = 'oip-line-color-inp';
    inp.dataset.colorKey = key;
    inp.title = 'Line color';
    _oipWireColorInput(inp);
    return inp;
}

// Wires an ALREADY-IN-THE-DOM <select class="oip-line-width-sel" data-width-key="...">.
function _oipWireWidthSelect(sel) {
    if (sel.dataset.wired) return;
    sel.dataset.wired = '1';
    const key = sel.dataset.widthKey;
    sel.value = String(oipGetLineWidth(key));
    sel.addEventListener('click', e => e.stopPropagation());
    sel.addEventListener('change', e => {
        e.stopPropagation();
        oipLineWidths[key] = parseInt(sel.value, 10);
        _oipSaveLineWidths();
        oipApplyLineStyleChange(key);
    });
}

function _oipBuildWidthSelect(key) {
    const sel = document.createElement('select');
    sel.className = 'oip-line-width-sel';
    sel.dataset.widthKey = key;
    sel.title = 'Line width';
    [1, 2, 3, 4].forEach(w => {
        const opt = document.createElement('option');
        opt.value = w; opt.textContent = w + 'px';
        sel.appendChild(opt);
    });
    _oipWireWidthSelect(sel);
    return sel;
}

// Wires an ALREADY-IN-THE-DOM <select class="oip-line-opacity-sel" data-opacity-key="...">.
function _oipWireOpacitySelect(sel) {
    if (sel.dataset.wired) return;
    sel.dataset.wired = '1';
    const key = sel.dataset.opacityKey;
    sel.value = String(oipGetLineOpacity(key));
    sel.addEventListener('click', e => e.stopPropagation());
    sel.addEventListener('change', e => {
        e.stopPropagation();
        oipLineOpacities[key] = parseFloat(sel.value);
        _oipSaveLineOpacities();
        oipApplyLineStyleChange(key);
    });
}

function _oipBuildOpacitySelect(key) {
    const sel = document.createElement('select');
    sel.className = 'oip-line-opacity-sel';
    sel.dataset.opacityKey = key;
    sel.title = 'Background opacity';
    [0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.18, 0.20].forEach(v => {
        const opt = document.createElement('option');
        opt.value = v; opt.textContent = Math.round(v * 100) + '%';
        sel.appendChild(opt);
    });
    _oipWireOpacitySelect(sel);
    return sel;
}

// Builds the full per-line control group (color + width + style, right-aligned
// as one unit) — or just style, for the 2 keys excluded from color/width
// (rsi, reversal1d — see _OIP_NO_COLOR_KEYS comment above). Box indicators
// (box30s/box5m) also get a background-opacity select for their filled rect.
function _oipBuildLinePropsGroup(key) {
    const wrap = document.createElement('span');
    wrap.className = 'oip-line-props';
    if (_OIP_COLOR_ONLY_KEYS.has(key)) {
        wrap.appendChild(_oipBuildColorInput(key));
        return wrap;
    }
    if (!_OIP_NO_COLOR_KEYS.has(key)) {
        wrap.appendChild(_oipBuildColorInput(key));
        if (_OIP_FILL_OPACITY_KEYS.has(key)) wrap.appendChild(_oipBuildOpacitySelect(key));
        wrap.appendChild(_oipBuildWidthSelect(key));
    }
    wrap.appendChild(_oipBuildStyleSelect(key));
    return wrap;
}

function oipInjectLineStyleSelectors() {
    _oipLoadLineStyles();
    _oipLoadLineColorsWidths();
    _oipResetCamarillaOverrides();
    _oipLoadLineOpacities();
    _OIP_LINE_STYLE_ITEMS.forEach(({ key, checkboxId }) => {
        const cb = document.getElementById(checkboxId);
        const label = cb?.closest('.oip-ind-item, .oip-ind-sub-item');
        // Already-injected check has to look for the colour input too — a
        // _OIP_COLOR_ONLY_KEYS row has no style select to find (see below).
        if (!label || label.querySelector(`[data-style-key="${key}"], [data-color-key="${key}"]`)) return;
        label.appendChild(_oipBuildLinePropsGroup(key));
    });
    // Synthetic value's 3 sub-lines: append a small row of control groups after its label.
    const synthCb = document.getElementById('oipShowSynthetic');
    const synthLabel = synthCb?.closest('.oip-ind-item');
    if (synthLabel && !document.getElementById('oipSynthStyleRow')) {
        const row = document.createElement('div');
        row.id = 'oipSynthStyleRow';
        row.style.cssText = 'display:flex;flex-direction:column;gap:3px;padding:2px 12px 6px 30px;';
        _OIP_SYNTH_STYLE_ITEMS.forEach(({ key, label }) => {
            // Full-width row: fixed-width label so Diff/PDC/C-P's controls all
            // start at the same x-position, with the props group pushed to the
            // popup's right edge (same alignment every other row uses).
            const wrap = document.createElement('span');
            wrap.style.cssText = 'display:flex;align-items:center;width:100%;font-size:10px;color:var(--oip-metric-lbl);';
            const lbl = document.createElement('span');
            lbl.textContent = label;
            lbl.style.cssText = 'width:28px;flex-shrink:0;';
            wrap.appendChild(lbl);
            wrap.appendChild(_oipBuildLinePropsGroup(key));
            row.appendChild(wrap);
        });
        synthLabel.insertAdjacentElement('afterend', row);
    }

    // Wire any hand-written controls already in the HTML (e.g. the Fixed Chart
    // Lines rows) — the ones just created above are already wired and skipped.
    document.querySelectorAll('.oip-line-style-sel').forEach(_oipWireStyleSelect);
    document.querySelectorAll('.oip-line-color-inp').forEach(_oipWireColorInput);
    document.querySelectorAll('.oip-line-width-sel').forEach(_oipWireWidthSelect);
    document.querySelectorAll('.oip-line-opacity-sel').forEach(_oipWireOpacitySelect);
    document.querySelectorAll('.oip-vol-dircolor-cb').forEach(_oipWireVolDirColorCheckbox);
}

// key -> array of persistent series objects (addSeries-based, reused via
// applyOptions). Recreated-on-redraw indicators (CPR/Multi CPR/Max Pain/
// Synthetic/ATM CE OI/reversal lines) are handled separately below since
// their lineStyle is read fresh inside their own draw function each redraw.
function _oipLineStyleSeriesMap() {
    return {
        cvwap:    [oipCvwapSeries, oipCvwapIntSeries, oipCvwapIntPeSeries, oipCECvwapSeries, oipPECvwapSeries],
        pvwap:    [oipPvwapSeries, oipPvwapIntSeries, oipPvwapIntPeSeries, oipCEPvwapSeries, oipPEPvwapSeries],
        avg3vwap: [oipAvg3VwapSeries, oipAvg3VwapIntSeries, oipAvg3VwapIntPeSeries, oipCEAvg3VwapSeries, oipPEAvg3VwapSeries],
        ema9:     [oipEma9Series, oipCEEma9Series, oipPEEma9Series],
        ema20:    [oipEma20Series, oipCEEma20Series, oipPEEma20Series],
        ema50:    [oipEma50Series, oipCEEma50Series, oipPEEma50Series],
        ema100:   [oipEma100Series],
        ema200:   [oipEma200Series],
        rsi:      oipRSISeriesObj ? Object.values(oipRSISeriesObj) : [],
        fixedCeAvg:   [oipFixedCeHL2Series],
        fixedPeAvg:   [oipFixedPeHL2Series],
        fixedCePeAvg: [oipFixedCloseAvgSeries],
    };
}

// Re-applies every persisted line style to already-created series. Needed
// because charts are built (oipInitCharts) before the popup loads persisted
// styles (oipInjectLineStyleSelectors, called from oipInitIndicatorsPopup) —
// same ordering issue oipSyncVwapVisibility already works around.
// Applies color + width + style together for keys that DO have a color
// picker; no-color keys (rsi, reversal1d) only get style.
function _oipApplyLineProps(s, key) {
    if (!s) return;
    const opts = { lineStyle: oipGetLineStyle(key) };
    if (!_OIP_NO_COLOR_KEYS.has(key)) {
        opts.color = oipGetLineColor(key);
        opts.lineWidth = oipGetLineWidth(key);
    }
    try { s.applyOptions(opts); } catch(e) {}
}

function oipApplyAllLineStyles() {
    const seriesMap = _oipLineStyleSeriesMap();
    Object.keys(seriesMap).forEach(key => {
        (seriesMap[key] || []).forEach(s => _oipApplyLineProps(s, key));
    });
}

function oipApplyLineStyleChange(key) {
    // Volume bars aren't a line series — colour lives in the data points, so
    // repaint them from the cache rather than going through applyOptions.
    if (_OIP_VOL_COLOR_KEY_SET.has(key)) { oipRepaintAllVolumeBars(); return; }
    const seriesMap = _oipLineStyleSeriesMap();
    if (key in seriesMap) {
        (seriesMap[key] || []).forEach(s => _oipApplyLineProps(s, key));
        return;
    }
    // Recreated-on-redraw indicators — re-run their draw function so the new
    // style (read via oipGetLineStyle inside each) takes effect immediately.
    if (key.startsWith('cpr')) { if (oipOIData?.candles) oipDrawCpr(oipOIData.candles); return; }
    if (key.startsWith('multiCpr')) { if (oipOIData?.candles) oipDrawMultiCPR(oipOIData.candles); return; }
    if (key === 'maxPain') { if (typeof oipUpdateMaxPainLine === 'function') oipUpdateMaxPainLine(oipCurrentPrice, oipOIData?.max_pain); return; }
    if (key.startsWith('synth')) { if (typeof oipDrawPremStrikeLines === 'function') oipDrawPremStrikeLines(); return; }
    if (key === 'atmCeOi') { oipDrawAtmCeOiLines(); return; }
    if (key === 'reversal30m') { if (typeof oipFullCandles !== 'undefined' && oipFullCandles) oipDraw30mReversalLines(oipFullCandles); return; }
    if (key === 'reversal1d')  { if (typeof oipFullCandles !== 'undefined' && oipFullCandles) oipDraw1DReversalLines(oipFullCandles); return; }
    if (key === 'box30s') { if (oipOIData?.candles) oipDraw2ndCandle30sBox(oipOIData.candles); return; }
    if (key === 'box5m')  { if (oipOIData?.candles) oipDraw2nd5mCandleBox(oipOIData.candles); return; }
    if (key === 'mondayBox') { if (oipOIData?.candles) oipDrawMondayBox(oipOIData.candles); return; }
    // 5m Close Border lives in the candle data itself, not a separate series —
    // re-push the candles so the new colour lands (see oi_profile.js).
    // typeof-guarded: this file is also loaded by dashboard/replay, which don't
    // pull in oi_profile.js (where these live) — same convention as maxPain above.
    if (key === 'fiveMClose') { if (typeof oipRedraw5mCloseMain === 'function') oipRedraw5mCloseMain(); return; }
    if (key === 'fiveMCloseOpt') { if (typeof oipRedraw5mCloseOpt === 'function') oipRedraw5mCloseOpt(); return; }
}

/* ── 5m Close Border ──────────────────────────────────────── */
// On sub-5-minute intervals the eye can't tell where one 5-minute candle ends
// and the next begins. Every bar that CLOSES a 5-minute block (on 1m: the
// :19 / :24 / :29 … bar, i.e. the 5th of each group) gets a distinct border
// colour while its body keeps its normal up/down colour — read the chart at
// 1-minute, but still see the 5-minute structure.
//
// Only intervals that divide 5 minutes evenly can have such a bar (30s and 1m);
// 2m/3m bars straddle the boundary, so those are left alone, as is anything
// already 5m or coarser. Note the newest bar is marked while it is still
// forming — that is the point: it flags "the 5-minute candle closes on THIS bar".
//
// Pure function (no DOM reads) so each block can resolve its own toggle and
// colour: the main chart and Opt Prem use the indicator popups' own checkboxes
// + oipGetLineColor keys, Round Strike uses its own local pickers.
//
// `interval` must likewise be passed in rather than read off the global — which
// bar closes a 5-minute block depends entirely on the bar width, and Round
// Strike runs on its own timeframe (oipRSInterval) now, independent of the
// oipInterval the rest of the page follows. Defaults to oipInterval for the
// callers that are on it.
const _OIP_5M_CLOSE_BAR_SECONDS = { '30second': 30, 'minute': 60 };

function oipMark5mCloseBorders(candles, enabled, color, interval) {
    if (!Array.isArray(candles) || !enabled) return candles;
    const barSec = _OIP_5M_CLOSE_BAR_SECONDS[interval ?? oipInterval];
    if (!barSec) return candles;
    // Timestamps are epoch seconds already shifted to IST (see the charts'
    // Etc/UTC formatter); the shift is 19800s, a whole multiple of 300, so a
    // 5-minute boundary is still just `epoch % 300 === 0`.
    return candles.map(c => {
        const t = Number(c.time ?? c.date);
        if (!isFinite(t)) return c;
        return ((t + barSec) % 300 === 0) ? { ...c, borderColor: color } : c;
    });
}

// Bars the backend rebuilt locally because Fyers' intraday history had nothing
// for today (their store went empty on 2026-08-03 while quotes stayed live) are
// flagged `synthetic` by /api/oi-profile/candles. They come from ~60s OI
// snapshots plus the quote poll, not from an exchange feed, so a 1-minute bar
// there is often a single sample with open == high == low == close.
//
// Tint their wicks so it is obvious on the chart exactly where real candles
// stop and the reconstruction begins — reading a flat-wicked stretch as real
// price structure would be badly misleading.
const _OIP_SYNTHETIC_WICK = '#f59e0b';

function oipMarkSynthetic(candles) {
    if (!Array.isArray(candles)) return candles;
    return candles.map(c => (c && c.synthetic)
        ? { ...c, wickColor: _OIP_SYNTHETIC_WICK }
        : c);
}

// True if any bar in the set was locally rebuilt — drives the chart banner.
function oipHasSynthetic(candles) {
    return Array.isArray(candles) && candles.some(c => c && c.synthetic);
}

// Shows/hides a banner above the OI Profile chart explaining that the newest
// bars are reconstructed. Created on demand so no template change is needed.
function oipSetSyntheticBanner(show) {
    let el = document.getElementById('oipSyntheticBanner');
    if (!show) {
        if (el) el.style.display = 'none';
        return;
    }
    if (!el) {
        const wrap = document.getElementById('oipChartWrap');
        if (!wrap) return;
        el = document.createElement('div');
        el.id = 'oipSyntheticBanner';
        el.style.cssText = [
            'padding:4px 10px', 'margin:0 0 4px', 'border-radius:4px',
            'font-size:11px', 'font-weight:600', 'line-height:1.4',
            `background:${_OIP_SYNTHETIC_WICK}22`, `color:${_OIP_SYNTHETIC_WICK}`,
            `border:1px solid ${_OIP_SYNTHETIC_WICK}66`,
        ].join(';');
        el.textContent = "⚠ Fyers has no intraday candles for today — the amber-wicked bars "
            + "are rebuilt from OI snapshots and the quote feed (~60s sampling), not exchange data. "
            + "Live algos are unaffected: they ignore these bars.";
        wrap.parentNode.insertBefore(el, wrap);
    }
    el.style.display = '';
}

// Removes a previously applied tag. Needed where candles are read back OUT of
// a series that already has them tagged (oipOISeries.data() feeding the Opt
// Prem index view) and must be re-tagged under a different popup's settings.
function oipStrip5mCloseBorder(candles) {
    if (!Array.isArray(candles)) return candles;
    return candles.map(c => {
        if (!c || c.borderColor === undefined) return c;
        const { borderColor, ...rest } = c;
        return rest;
    });
}

// Resolves the toggle + colour for one of the two popup-driven instances.
// which: 'main' (OI Profile chart) | 'opt' (Opt Prem CE/PE/Combined charts).
function oip5mCloseSettings(which) {
    const isOpt = which === 'opt';
    return {
        enabled: document.getElementById(isOpt ? 'oipShowOpt5mClose' : 'oipShow5mClose')?.checked ?? false,
        color: oipGetLineColor(isOpt ? 'fiveMCloseOpt' : 'fiveMClose')
    };
}

/* ── EMA visibility ───────────────────────────────────────── */
// Main chart ONLY — controlled by the main Indicators popup's EMA checkboxes.
function oipUpdateEmaVisibility() {
    const s9   = oipElems.showEma9?.checked   ?? false;
    const s20  = oipElems.showEma20?.checked  ?? false;
    const s50  = oipElems.showEma50?.checked  ?? false;
    const s100 = oipElems.showEma100?.checked ?? false;
    const s200 = oipElems.showEma200?.checked ?? false;

    if (oipEma9Series)   oipEma9Series.applyOptions({ visible: s9 });
    if (oipEma20Series)  oipEma20Series.applyOptions({ visible: s20 });
    if (oipEma50Series)  oipEma50Series.applyOptions({ visible: s50 });
    if (oipEma100Series) oipEma100Series.applyOptions({ visible: s100 });
    if (oipEma200Series) oipEma200Series.applyOptions({ visible: s200 });
}

// CE Only / PE Only charts ONLY — controlled INDEPENDENTLY by the Opt
// Indicator popup's own EMA 9/20/50 checkboxes (previously these silently
// mirrored the main popup's checkboxes, so CE/PE always looked "identical"
// to whatever the main chart's EMA state was — there was no separate switch).
function oipUpdateOptEmaVisibility() {
    const s9  = document.getElementById('oipShowEma9Opt')?.checked  ?? false;
    const s20 = document.getElementById('oipShowEma20Opt')?.checked ?? false;
    const s50 = document.getElementById('oipShowEma50Opt')?.checked ?? false;

    // Defer past the CE/PE charts' init RAF — applyOptions triggers LC's async
    // render RAF which crashes if the chart isn't yet initialized.
    requestAnimationFrame(() => {
        try { if (oipCEEma9Series)  oipCEEma9Series.applyOptions({ visible: s9 }); } catch(e) {}
        try { if (oipCEEma20Series) oipCEEma20Series.applyOptions({ visible: s20 }); } catch(e) {}
        try { if (oipCEEma50Series) oipCEEma50Series.applyOptions({ visible: s50 }); } catch(e) {}
        try { if (oipPEEma9Series)  oipPEEma9Series.applyOptions({ visible: s9 }); } catch(e) {}
        try { if (oipPEEma20Series) oipPEEma20Series.applyOptions({ visible: s20 }); } catch(e) {}
        try { if (oipPEEma50Series) oipPEEma50Series.applyOptions({ visible: s50 }); } catch(e) {}
    });
}

/* ── Calculation functions ────────────────────────────────── */
function oipCalculateFixedEMA(data, period) {
    if (!data || data.length === 0) return [];
    const ema = [];
    const k = 2 / (period + 1);
    let prevEma = data[0].close;

    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        const val = (d.close * k) + (prevEma * (1 - k));
        if (!isNaN(val)) {
            ema.push({ time: d.time, value: val });
            prevEma = val;
        }
    });
    return ema;
}

// Computes EMA9/20/50/100/200 in a single pass — 5x fewer iterations than calling oipCalculateFixedEMA five times.
function oipCalculateAllEMAs(data) {
    if (!data || data.length === 0) return { ema9: [], ema20: [], ema50: [], ema100: [], ema200: [] };
    const periods = [9, 20, 50, 100, 200];
    const k = periods.map(p => 2 / (p + 1));
    const emas = periods.map(() => []);
    let prev = periods.map(() => data[0].close);
    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        for (let i = 0; i < 5; i++) {
            const val = d.close * k[i] + prev[i] * (1 - k[i]);
            if (!isNaN(val)) { emas[i].push({ time: d.time, value: val }); prev[i] = val; }
        }
    });
    return { ema9: emas[0], ema20: emas[1], ema50: emas[2], ema100: emas[3], ema200: emas[4] };
}

// Computes EMA9/20/50 in a single pass for CE/PE option charts.
function oipCalculate3EMAs(data) {
    if (!data || data.length === 0) return { ema9: [], ema20: [], ema50: [] };
    const periods = [9, 20, 50];
    const k = periods.map(p => 2 / (p + 1));
    const emas = periods.map(() => []);
    let prev = periods.map(() => data[0].close);
    data.forEach(d => {
        if (d.close == null || isNaN(d.close)) return;
        for (let i = 0; i < 3; i++) {
            const val = d.close * k[i] + prev[i] * (1 - k[i]);
            if (!isNaN(val)) { emas[i].push({ time: d.time, value: val }); prev[i] = val; }
        }
    });
    return { ema9: emas[0], ema20: emas[1], ema50: emas[2] };
}

// Anchored VWAP: the cumulation restarts at the top of each anchor period, so
// 30s–30m give the session VWAP, 1h a weekly one, 1D monthly, 1W/1M yearly —
// the same periods CPR uses (see oipAnchorPeriod).
function oipCalculateVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    const anchor = oipAnchorPeriod();
    let cumPV = 0, cumV = 0, lastDate = null;
    const result = [];
    candles.forEach(c => {
        const date = _oipPeriodKey(c.time, anchor);
        if (date !== lastDate) { cumPV = 0; cumV = 0; lastDate = date; }
        const vol = c.volume || 0;
        if (vol <= 0) return;
        cumPV += ((c.high + c.low + c.close) / 3) * vol;
        cumV += vol;
        const vwapVal = cumPV / cumV;
        if (!isNaN(vwapVal)) {
            result.push({ time: c.time, value: vwapVal });
        }
    });
    return result;
}

// CVWAP — alias for the current-period VWAP (the trading day on an intraday
// timeframe; see oipCalculateVWAP). Kept as a thin wrapper so the indicator
// wiring reads CVWAP/PVWAP symmetrically.
function oipCalculateCVWAP(candles) {
    return oipCalculateVWAP(candles);
}

// PVWAP — Previous-session VWAP. For every candle of a given day the value is the
// FINAL (closing) VWAP of the *previous* trading day, drawn as a flat line across
// the current session. Mirrors the "Previous VWAP" plot in the
// "Current & Previous VWAP Strategy" Pine script.
function oipCalculatePVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    // "Previous session" is the previous ANCHOR period — the trading day on an
    // intraday timeframe, the previous week/month/year on the higher ones.
    const anchor = oipAnchorPeriod();
    const dateOf = (t) => _oipPeriodKey(t, anchor);

    // Pass 1 — final VWAP per day, preserving day order.
    const finalVwap = {};
    const dayOrder = [];
    let cumPV = 0, cumV = 0, lastDate = null, lastVwap = null;
    candles.forEach(c => {
        const date = dateOf(c.time);
        if (date !== lastDate) {
            if (lastDate !== null) finalVwap[lastDate] = lastVwap;
            cumPV = 0; cumV = 0; lastVwap = null; lastDate = date;
            dayOrder.push(date);
        }
        const vol = c.volume || 0;
        if (vol <= 0) return;
        cumPV += ((c.high + c.low + c.close) / 3) * vol;
        cumV += vol;
        const v = cumPV / cumV;
        if (!isNaN(v)) lastVwap = v;
    });
    if (lastDate !== null) finalVwap[lastDate] = lastVwap;

    // Map each day to the previous day's final VWAP.
    const prevDayVwap = {};
    for (let i = 1; i < dayOrder.length; i++) {
        prevDayVwap[dayOrder[i]] = finalVwap[dayOrder[i - 1]];
    }

    // Pass 2 — emit a flat previous-day VWAP line for each candle.
    const result = [];
    candles.forEach(c => {
        const pv = prevDayVwap[dateOf(c.time)];
        if (pv != null && !isNaN(pv)) result.push({ time: c.time, value: pv });
    });
    return result;
}

// 3-AVG_VWAP — average of the FINAL VWAP of the 3 PREVIOUS trading days, drawn
// as a flat line across the current session (same convention as PVWAP above,
// and the "Avg 3 VWAP" column / Pine CPR script's Avg 3 VWAP plot).
function oipCalculateAvg3VWAP(candles) {
    if (!candles || candles.length === 0) return [];
    // Three previous ANCHOR periods, matching PVWAP above.
    const anchor = oipAnchorPeriod();
    const dateOf = (t) => _oipPeriodKey(t, anchor);

    // Pass 1 — final VWAP per day, preserving day order (mirrors PVWAP's pass 1).
    const finalVwap = {};
    const dayOrder = [];
    let cumPV = 0, cumV = 0, lastDate = null, lastVwap = null;
    candles.forEach(c => {
        const date = dateOf(c.time);
        if (date !== lastDate) {
            if (lastDate !== null) finalVwap[lastDate] = lastVwap;
            cumPV = 0; cumV = 0; lastVwap = null; lastDate = date;
            dayOrder.push(date);
        }
        const vol = c.volume || 0;
        if (vol <= 0) return;
        cumPV += ((c.high + c.low + c.close) / 3) * vol;
        cumV += vol;
        const v = cumPV / cumV;
        if (!isNaN(v)) lastVwap = v;
    });
    if (lastDate !== null) finalVwap[lastDate] = lastVwap;

    // Map each day to the average of the 3 PRECEDING days' final VWAP.
    const avg3Vwap = {};
    for (let i = 3; i < dayOrder.length; i++) {
        const v1 = finalVwap[dayOrder[i - 1]], v2 = finalVwap[dayOrder[i - 2]], v3 = finalVwap[dayOrder[i - 3]];
        if (v1 != null && v2 != null && v3 != null && !isNaN(v1) && !isNaN(v2) && !isNaN(v3)) {
            avg3Vwap[dayOrder[i]] = (v1 + v2 + v3) / 3;
        }
    }

    // Pass 2 — emit a flat 3-day-average VWAP line for each candle.
    const result = [];
    candles.forEach(c => {
        const av = avg3Vwap[dateOf(c.time)];
        if (av != null && !isNaN(av)) result.push({ time: c.time, value: av });
    });
    return result;
}

/* ── Fixed-strike chart: previous-day reference lines ────────
   Same "final value per day, held flat across the NEXT session" convention
   as PVWAP above. Used for the 24000-strike monthly-expiry combined chart. */
function _oipGroupCandlesByDay(candles) {
    const map = {};
    const order = [];
    (candles || []).forEach(c => {
        const d = new Date(c.time * 1000);
        // UTC methods match the 'Fake IST Epoch' the server emits.
        const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        if (!map[key]) { map[key] = []; order.push(key); }
        map[key].push(c);
    });
    return { map, order };
}

// Previous day's (High + Low) / 2 for a single option leg, drawn as a flat
// line across the current session.
function oipCalculatePrevDayHL2(candles) {
    if (!candles || !candles.length) return [];
    const { map, order } = _oipGroupCandlesByDay(candles);

    const dayVal = {};
    order.forEach(day => {
        const dc = map[day];
        const high = Math.max(...dc.map(c => c.high));
        const low  = Math.min(...dc.map(c => c.low));
        if (!isNaN(high) && !isNaN(low)) dayVal[day] = (high + low) / 2;
    });

    const prevVal = {};
    for (let i = 1; i < order.length; i++) {
        if (dayVal[order[i - 1]] != null) prevVal[order[i]] = dayVal[order[i - 1]];
    }

    const result = [];
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        const v = prevVal[key];
        if (v != null && !isNaN(v)) result.push({ time: c.time, value: v });
    });
    return result;
}

// Previous day's (CE close + PE close) / 2, drawn as a flat line across the
// current session. ceCandles/peCandles are expected to share the same
// trading-day boundaries (both come from the same fetch).
function oipCalculatePrevDayCloseAvg(ceCandles, peCandles) {
    if (!ceCandles?.length || !peCandles?.length) return [];
    const ce = _oipGroupCandlesByDay(ceCandles);
    const pe = _oipGroupCandlesByDay(peCandles);

    const dayVal = {};
    ce.order.forEach(day => {
        const peDay = pe.map[day];
        if (!peDay || !peDay.length) return;
        const ceClose = ce.map[day][ce.map[day].length - 1].close;
        const peClose = peDay[peDay.length - 1].close;
        if (!isNaN(ceClose) && !isNaN(peClose)) dayVal[day] = (ceClose + peClose) / 2;
    });

    const prevVal = {};
    for (let i = 1; i < ce.order.length; i++) {
        if (dayVal[ce.order[i - 1]] != null) prevVal[ce.order[i]] = dayVal[ce.order[i - 1]];
    }

    const result = [];
    ceCandles.forEach(c => {
        const d = new Date(c.time * 1000);
        const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        const v = prevVal[key];
        if (v != null && !isNaN(v)) result.push({ time: c.time, value: v });
    });
    return result;
}

function oipCalculateDynamicEMA(candles, interval) {
    if (!candles || !candles.length) return [];
    let len = null;
    if (interval === 'minute') len = 60;
    else if (interval === '2minute') len = 187;
    else if (interval === '3minute') len = 125;
    else if (interval === '5minute') len = 75;
    else if (interval === '15minute') len = 125;
    else if (interval === '30minute') len = 125;
    else if (interval === '60minute') len = 137;
    else len = 88;
    if (len === null) return [];
    const k = 2 / (len + 1);
    let ema = null;
    return candles.map(c => {
        if (ema === null) ema = c.close;
        else ema = (c.close - ema) * k + ema;
        return { time: c.time, value: ema };
    });
}

/* ── Anchor period ────────────────────────────────────────────────────────
   Which period the timeframe-anchored indicators reset on. CPR computes its
   pivots from the PREVIOUS period; VWAP restarts its cumulation at the start
   of each one:

     30s … 30m  →  daily    (yesterday's H/L/C)
     1h         →  weekly   (last week's)
     1D         →  monthly  (last month's)
     1W, 1M     →  yearly   (last year's)

   CPR therefore needs at least one completed prior period in the chart for any
   levels to exist: on 1W/1M that means the date range must reach back into the
   previous calendar year. VWAP has no such requirement — it just anchors to the
   start of whatever period a candle falls in. */
const _OIP_ANCHOR_BY_INTERVAL = {
    '30second': 'day', 'minute': 'day', '2minute': 'day', '3minute': 'day',
    '5minute': 'day', '15minute': 'day', '30minute': 'day',
    '60minute': 'week',
    'day': 'month',
    'week': 'year', 'month': 'year'
};
const _OIP_ANCHOR_LABELS = { day: 'Daily', week: 'Weekly', month: 'Monthly', year: 'Yearly' };

// The <select> is the source of truth; oipInterval (declared per page in
// oi_profile.js / oi_replay.js) is the fallback for pages without one.
function _oipActiveInterval() {
    const fromDom = document.getElementById('oipInterval')?.value;
    if (fromDom) return fromDom;
    try { return oipInterval; } catch (e) { return 'minute'; }
}

function oipAnchorPeriod(interval) {
    return _OIP_ANCHOR_BY_INTERVAL[interval ?? _oipActiveInterval()] || 'day';
}

// Period key for one candle. UTC methods throughout, to match the server's
// 'Fake IST Epoch'. Weeks are Monday-anchored (keyed by that Monday's date).
function _oipPeriodKey(time, anchor) {
    const d = new Date(time * 1000);
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    if (anchor === 'year')  return `${y}`;
    if (anchor === 'month') return `${y}-${m}`;
    if (anchor === 'week') {
        const monday = new Date(Date.UTC(y, d.getUTCMonth(), d.getUTCDate()));
        // getUTCDay: 0=Sun … 6=Sat — shift Sunday back six days, not forward one.
        monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
        return monday.toISOString().split('T')[0];
    }
    return `${y}-${m}-${day}`;
}

// Keeps the popup's "CPR Levels" row naming the period actually being drawn,
// so a 1h chart showing week pivots does not read as a broken daily CPR.
function _oipUpdateCprAnchorLabel(anchor) {
    const el = document.querySelector('label[for="oipShowCpr"]');
    if (el) el.textContent = `CPR Levels · ${_OIP_ANCHOR_LABELS[anchor] || 'Daily'}`;
}

function oipCalculateDynamicCPR(candles) {
    if (!candles || !candles.length) return null;
    const anchor = oipAnchorPeriod();
    _oipUpdateCprAnchorLabel(anchor);

    const periods = []; let current = null;
    candles.forEach(c => {
        const key = _oipPeriodKey(c.time, anchor);
        if (!current || current.key !== key) {
            if (current) periods.push(current);
            // isoDate is the period's FIRST calendar day — only meaningful for
            // the daily anchor, where it keys into the server's daily_ohlc.
            current = { key, isoDate: _oipPeriodKey(c.time, 'day'), high: c.high, low: c.low, close: c.close, times: [], closes: [] };
        }
        current.high = Math.max(current.high, c.high);
        current.low = Math.min(current.low, c.low);
        current.close = c.close;
        current.times.push(c.time);
        current.closes.push(c.close);
    });
    if (current) periods.push(current);

    let daysData = [];
    for (let i = 1; i < periods.length; i++) {
        const prev = periods[i - 1], curr = periods[i];
        let oH = prev.high, oL = prev.low, oC = prev.close;
        // The server's true daily OHLC beats bars aggregated from an intraday
        // feed that may not cover the whole session. It is per-DAY data, so it
        // only stands in for the daily anchor.
        if (anchor === 'day' && oipOIData?.daily_ohlc?.[prev.isoDate]) {
            const t = oipOIData.daily_ohlc[prev.isoDate]; oH = t.high; oL = t.low; oC = t.close;
        }
        const pp = (oH + oL + oC) / 3;
        const bc = (oH + oL) / 2;
        const tc = (pp - bc) + pp;

        const range = oH - oL;
        const r1 = (pp * 2) - oL;
        const s1 = (pp * 2) - oH;
        const r2 = pp + range;
        const s2 = pp - range;
        const r3 = r1 + range;
        const s3 = s1 - range;
        const r4 = r3 + (r2 - r1);
        const s4 = s3 - (s1 - s2);

        const cr3 = oC + (range * 1.1) / 4;
        const cs3 = oC - (range * 1.1) / 4;

        let boxes = [
            { type: 'cpr', min: Math.min(tc, bc), max: Math.max(tc, bc), times: curr.times }
        ];

        daysData.push({
            times: curr.times,
            levels: { prevH: oH, prevL: oL, r1, r2, r3, r4, s1, s2, s3, s4, cr3, cs3, pp, tc, bc },
            boxes: boxes
        });
    }
    return daysData;
}

function oipCalculateRSISnR(candles) {
    if (!candles || candles.length < 15) return null;
    const len = 14;
    const rsi = new Array(candles.length).fill(null);
    let gains = 0, losses = 0;

    for (let i = 1; i <= len; i++) {
        const diff = candles[i].close - candles[i - 1].close;
        if (diff >= 0) gains += diff;
        else losses -= diff;
    }
    let avgGain = gains / len;
    let avgLoss = losses / len;
    rsi[len] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));

    for (let i = len + 1; i < candles.length; i++) {
        const diff = candles[i].close - candles[i - 1].close;
        const gain = diff >= 0 ? diff : 0;
        const loss = diff < 0 ? -diff : 0;
        avgGain = (avgGain * (len - 1) + gain) / len;
        avgLoss = (avgLoss * (len - 1) + loss) / len;
        rsi[i] = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
    }

    let level_ob = null, level_os = null, level_bull = null, level_bear = null;
    const ob_series = [], os_series = [], bull_series = [], bear_series = [];
    const markers = [];

    const thresh_ob = 70, thresh_bull = 60, thresh_bear = 40, thresh_os = 30;
    let in_short = false, in_long = false;
    let short_sl = null, long_sl = null;

    const L_OB = new Array(candles.length).fill(null);
    const L_OS = new Array(candles.length).fill(null);

    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], r = rsi[i], r_prev = rsi[i - 1];
        if (r === null || r_prev === null) continue;

        const avgHigh = (c.high + c.close) / 2;
        const avgLow = (c.low + c.close) / 2;

        if (r_prev <= thresh_ob && r > thresh_ob) level_ob = avgHigh;
        if (r_prev >= thresh_os && r < thresh_os) level_os = avgLow;
        if ((r_prev <= thresh_bull && r > thresh_bull) || (r_prev >= thresh_bull && r < thresh_bull)) level_bull = avgHigh;
        if ((r_prev <= thresh_bear && r > thresh_bear) || (r_prev >= thresh_bear && r < thresh_bear)) level_bear = avgLow;

        L_OB[i] = level_ob;
        L_OS[i] = level_os;

        if (level_ob !== null) ob_series.push({ time: c.time, value: level_ob });
        if (level_os !== null) os_series.push({ time: c.time, value: level_os });
        if (level_bull !== null) bull_series.push({ time: c.time, value: level_bull });
        if (level_bear !== null) bear_series.push({ time: c.time, value: level_bear });

        if (i >= 2) {
            const pC = candles[i - 1], ppC = candles[i - 2];
            const pL_OB = L_OB[i - 1], ppL_OB = L_OB[i - 2], pL_OS = L_OS[i - 1], ppL_OS = L_OS[i - 2];

            if (pL_OB !== null && i < candles.length - 1) {
                const setup_ob_rej = (pC.close < pL_OB) && (pC.high >= pL_OB || ppC.close >= ppL_OB);
                const short_entry = setup_ob_rej && (c.close < pC.low);
                if (short_entry) {
                    in_short = true; in_long = false; short_sl = pC.high;
                    markers.push({ time: c.time, position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'ENTRY' });
                }
            }
            if (pL_OS !== null && i < candles.length - 1) {
                const setup_os_rej = (pC.close > pL_OS) && (pC.low <= pL_OS || ppC.close <= ppL_OS);
                const long_entry = setup_os_rej && (c.close > pC.high);
                if (long_entry) {
                    in_long = true; in_short = false; long_sl = pC.low;
                    markers.push({ time: c.time, position: 'belowBar', color: '#14b8a6', shape: 'arrowUp', text: 'ENTRY' });
                }
            }

            if (in_short && i < candles.length - 1) {
                const tgtHit = level_bear !== null && c.low <= level_bear;
                const slHit = short_sl !== null && c.high >= short_sl;
                if (tgtHit || slHit) {
                    in_short = false;
                    markers.push({ time: c.time, position: 'aboveBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'S SL' });
                }
            }
            if (in_long && i < candles.length - 1) {
                const tgtHit = level_bull !== null && c.high >= level_bull;
                const slHit = long_sl !== null && c.low <= long_sl;
                if (tgtHit || slHit) {
                    in_long = false;
                    markers.push({ time: c.time, position: 'belowBar', color: tgtHit ? '#f97316' : '#800000', shape: tgtHit ? 'circle' : 'circle', text: tgtHit ? '🎯' : 'B SL' });
                }
            }
        }
    }
    return { ob_series, os_series, bull_series, bear_series, markers };
}

/* ── Draw / apply functions ───────────────────────────────── */
function oipUpdateAllMarkers() {
    if (!oipOISeries) return;
    const combined = [...oipSignalMarkers, ...oipRSIMarkers].sort((a, b) => a.time - b.time);
    lwSetMarkers(oipOISeries, combined);
}

/* ── CPR level labels & hierarchy ─────────────────────────────────────────
   A day of pivots is 15 lines, and they are redrawn per day segment. Two
   things make that readable: a name on each level, and a fade on the outer
   pivots so R1/S1 stay the ones the eye lands on.

   The label rides on the series' `title` — LightweightCharts only draws that
   where `lastValueVisible` is on, so it goes on the most recent day's segment
   alone; putting it on every day would stack one label per day down the axis.
   The fade multiplies the group's chosen colour rather than replacing it, so
   a user-picked colour still drives the hue. */
const _OIP_CPR_LABELS = {
    prevH: 'PH', prevL: 'PL', pp: 'PP', bc: 'BC', tc: 'TC',
    r1: 'R1', r2: 'R2', r3: 'R3', r4: 'R4',
    s1: 'S1', s2: 'S2', s3: 'S3', s4: 'S4',
    cr3: 'CR3', cs3: 'CS3'
};
const _OIP_CPR_LEVEL_ALPHA = { r2: 0.8, r3: 0.62, r4: 0.45, s2: 0.8, s3: 0.62, s4: 0.45 };

// Labels are a Replay feature by default: the checkbox exists in the Indicators
// popup on the dashboard and OI Profile, and the compact /replay toolbar has no
// popup to carry it, so there it falls back to on.
function _oipCprLabelsOn() {
    const el = document.getElementById('oipCprShowLabels');
    return el ? el.checked : !!window.oipReplayMode;
}

function _oipCprFade(color, alpha) {
    if (typeof color !== 'string') return color;
    if (color.startsWith('#') && color.length >= 7) {
        const r = parseInt(color.slice(1, 3), 16);
        const g = parseInt(color.slice(3, 5), 16);
        const b = parseInt(color.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }
    const m = color.match(/[\d.]+/g);
    if (m && m.length >= 3) return `rgba(${m[0]},${m[1]},${m[2]},${alpha})`;
    return color;
}

// The options every CPR level line gets — shared by the full redraw here and by
// the replay renderer in oi_replay.js, so both look the same.
function oipCprLineOptions(key, styleKey, labelled) {
    const alpha = _OIP_CPR_LEVEL_ALPHA[key];
    const color = oipGetLineColor(styleKey);
    const label = labelled ? (_OIP_CPR_LABELS[key] || '') : '';
    return {
        color: alpha ? _oipCprFade(color, alpha) : color,
        lineWidth: oipGetLineWidth(styleKey),
        lineStyle: oipGetLineStyle(styleKey),
        title: label,
        lastValueVisible: !!label
    };
}

/* ── CPR renderer ─────────────────────────────────────────────────────────
   ONE line series per level for the whole chart — not one per level per
   period. A level is flat inside its period, so the period only contributes
   three points to that level's series: its first bar, its last-but-one bar,
   and a whitespace point on the closing bar that breaks the line before the
   next period's level starts.

   The old shape was `line_<level>_<periodIndex>`, i.e. 15 series per period.
   Over a year of 5m bars that is ~3,800 series on one chart, every one of them
   cleared and refilled on every replay step — measured at 765 ms per step with
   only 60 days loaded, and it wedged the renderer outright at a year. The
   compact form is ~1 ms per step.

   The TC/BC band still needs a series per period: a BaselineSeries carries one
   baseValue and BC moves period to period. Those are created once and only the
   period under the playhead is rewritten as replay advances (_oipCprState). */
const _OIP_CPR_LEVEL_KEYS = ['prevH', 'prevL', 'pp', 'tc', 'bc', 'r1', 'r2', 'r3', 'r4', 's1', 's2', 's3', 's4', 'cr3', 'cs3'];

const _OIP_CPR_STYLE_KEY = {
    prevH: 'cprPrevHL', prevL: 'cprPrevHL',
    pp: 'cprBand', bc: 'cprBand', tc: 'cprBand',
    r1: 'cprResistance', r2: 'cprResistance', r3: 'cprResistance', r4: 'cprResistance',
    s1: 'cprSupport', s2: 'cprSupport', s3: 'cprSupport', s4: 'cprSupport',
    cr3: 'cprCumR3S3', cs3: 'cprCumR3S3'
};
const _OIP_CPR_CHECKBOX = {
    prevH: 'oipCprShowPrevHL', prevL: 'oipCprShowPrevHL',
    pp: 'oipCprShowBand', bc: 'oipCprShowBand', tc: 'oipCprShowBand',
    r1: 'oipCprShowResistance', r2: 'oipCprShowResistance', r3: 'oipCprShowResistance', r4: 'oipCprShowResistance',
    s1: 'oipCprShowSupport', s2: 'oipCprShowSupport', s3: 'oipCprShowSupport', s4: 'oipCprShowSupport',
    cr3: 'oipCprShowCumR3S3', cs3: 'oipCprShowCumR3S3'
};

// Tracks what is already on the chart, so a replay step can extend the live
// period instead of rebuilding every series.
let _oipCprState = { sig: '', liveIdx: -1, maxTime: -1, boxCount: {} };

function _oipCprSubChecked(id) { return document.getElementById(id)?.checked !== false; }

// Identifies the loaded dataset: a new symbol/timeframe/date range invalidates
// every cached series, a replay step does not.
function _oipCprSignature(daysData) {
    if (!daysData || !daysData.length) return 'empty';
    const f = daysData[0].times, l = daysData[daysData.length - 1].times;
    return `${daysData.length}|${f[0]}|${l[l.length - 1]}|${_oipActiveInterval()}`;
}

function _oipCprLineSeries(key) {
    const k = `line_${key}`;
    if (!oipCprSeriesMap[k]) {
        oipCprSeriesMap[k] = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => null
        });
    }
    return oipCprSeriesMap[k];
}

function _oipCprBoxSeries(idx, fill) {
    const k = `box_${idx}`;
    if (!oipCprSeriesMap[k]) {
        oipCprSeriesMap[k] = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
            topFillColor1: fill, topFillColor2: fill, topLineColor: 'transparent',
            bottomFillColor1: fill, bottomFillColor2: fill, bottomLineColor: 'transparent',
            lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => null
        });
    }
    return oipCprSeriesMap[k];
}

// Drops every cached CPR series off the chart. Called when the dataset changes
// — leaving stale period boxes behind would draw bands from the old range.
function oipClearCprSeries() {
    Object.keys(oipCprSeriesMap).forEach(k => {
        try { oipOIChart.removeSeries(oipCprSeriesMap[k]); } catch (e) {}
        delete oipCprSeriesMap[k];
    });
    _oipCprState = { sig: '', liveIdx: -1, maxTime: -1, boxCount: {} };
}

function _oipCprBlank() {
    Object.keys(oipCprSeriesMap).forEach(k => { try { oipCprSeriesMap[k].setData([]); } catch (e) {} });
    _oipCprState.liveIdx = -1; _oipCprState.maxTime = -1; _oipCprState.boxCount = {};
}

// How many of a period's bars are at or before the playhead.
function _oipVisibleCount(times, maxTime) {
    if (maxTime == null || maxTime === Infinity) return times.length;
    if (times[0] > maxTime) return 0;
    if (times[times.length - 1] <= maxTime) return times.length;
    let lo = 0, hi = times.length;                 // first index past maxTime
    while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] <= maxTime) lo = mid + 1; else hi = mid; }
    return lo;
}

/* Full redraw. maxTime clips to the replay playhead (Infinity = whole chart).
   Finished periods keep their band fill untouched — re-setting all of them was
   2.3 s per slider drag on a year of 5m bars, since each BaselineSeries reindexes
   its data and the whole stack then gets reordered. */
function oipRenderCprLevels(daysData, maxTime = Infinity) {
    if (!oipOIChart || !oipOISeries) return;
    const show = oipElems.showCpr?.checked;
    if (!show || !daysData || !daysData.length) { _oipCprBlank(); return; }

    const sig = _oipCprSignature(daysData);
    if (sig !== _oipCprState.sig) oipClearCprSeries();

    const labelled = _oipCprLabelsOn();
    const bandOn = _oipCprSubChecked('oipCprShowBand');
    const fill = _oipCprFade(oipGetLineColor('cprBand'), 0.14);
    const prev = _oipCprState;
    const keepBoxes = prev.sig === sig && prev.fill === fill && prev.bandOn === bandOn;
    const lineData = {}; _OIP_CPR_LEVEL_KEYS.forEach(k => lineData[k] = []);
    const boxCount = {};
    let liveIdx = -1, created = sig !== prev.sig;

    daysData.forEach((day, di) => {
        const times = day.times;
        const n = _oipVisibleCount(times, maxTime);
        if (!n) return;
        const complete = n === times.length;
        if (!complete) liveIdx = di;

        _OIP_CPR_LEVEL_KEYS.forEach(k => {
            const v = day.levels[k];
            if (v == null || isNaN(v)) return;
            const arr = lineData[k];
            arr.push({ time: times[0], value: v });
            if (complete) {
                // Whitespace on the closing bar breaks the line, so the next
                // period's level does not get joined to this one by a diagonal.
                if (n >= 3) arr.push({ time: times[n - 2], value: v });
                if (n >= 2) arr.push({ time: times[n - 1] });
            } else if (n >= 2) {
                arr.push({ time: times[n - 1], value: v });
            }
        });

        const box = day.boxes && day.boxes[0];
        if (!box) return;
        boxCount[di] = n;
        if (!bandOn) { if (oipCprSeriesMap[`box_${di}`]) oipCprSeriesMap[`box_${di}`].setData([]); return; }
        if (keepBoxes && prev.boxCount[di] === n && oipCprSeriesMap[`box_${di}`]) return;   // unchanged
        if (!oipCprSeriesMap[`box_${di}`]) created = true;
        const s = _oipCprBoxSeries(di, fill);
        s.applyOptions({
            baseValue: { type: 'price', price: box.min },
            topFillColor1: fill, topFillColor2: fill, bottomFillColor1: fill, bottomFillColor2: fill
        });
        s.setData(times.slice(0, n).map(t => ({ time: t, value: box.max })));
    });

    // Periods that fell past the playhead (a jump backwards) must not keep
    // showing their band.
    Object.keys(oipCprSeriesMap).forEach(k => {
        if (!k.startsWith('box_')) return;
        if (!(k.slice(4) in boxCount)) { try { oipCprSeriesMap[k].setData([]); } catch (e) {} }
    });

    _OIP_CPR_LEVEL_KEYS.forEach(k => {
        if (!oipCprSeriesMap[`line_${k}`]) created = true;
        const s = _oipCprLineSeries(k);
        s.applyOptions(oipCprLineOptions(k, _OIP_CPR_STYLE_KEY[k], labelled));
        s.setData(_oipCprSubChecked(_OIP_CPR_CHECKBOX[k]) ? lineData[k] : []);
    });

    _oipCprState = { sig, liveIdx, maxTime, boxCount, fill, bandOn };
    // Ordering only matters when the series stack itself changed.
    if (created) oipApplyZOrder();
}

/* Replay step. Extends the period under the playhead — and, when the playhead
   crosses into the next one, closes the old period off and opens the new one —
   without touching any finished period. Returns false when the caller has to
   fall back to a full redraw (new dataset, a jump backwards, or a jump of more
   than one period). */
function oipAdvanceCprLevels(daysData, maxTime) {
    if (!oipOIChart || !daysData || !daysData.length) return false;
    if (!oipElems.showCpr?.checked) return false;
    const st = _oipCprState;
    if (st.sig !== _oipCprSignature(daysData) || st.liveIdx < 0 || maxTime <= st.maxTime) return false;

    const visible = k => _oipCprSubChecked(_OIP_CPR_CHECKBOX[k]);
    let idx = st.liveIdx;
    const live = daysData[idx];
    if (!live) return false;

    let created = false;
    if (maxTime > live.times[live.times.length - 1]) {
        const next = daysData[idx + 1];
        // Only a step INTO the next period is cheap; anything further redraws.
        if (!next || maxTime < next.times[0] || maxTime > next.times[next.times.length - 1]) return false;
        // Whitespace on the finished period's closing bar breaks its line before
        // the new period's level starts (update() with an existing timestamp
        // replaces that point).
        const lastBar = live.times[live.times.length - 1];
        _OIP_CPR_LEVEL_KEYS.forEach(k => {
            if (!visible(k) || live.levels[k] == null) return;
            try { _oipCprLineSeries(k).update({ time: lastBar }); } catch (e) {}
        });
        idx += 1;
        if (!oipCprSeriesMap[`box_${idx}`]) created = true;
    }

    const day = daysData[idx];
    _OIP_CPR_LEVEL_KEYS.forEach(k => {
        const v = day.levels[k];
        if (v == null || isNaN(v) || !visible(k)) return;
        try { _oipCprLineSeries(k).update({ time: maxTime, value: v }); } catch (e) {}
    });

    const box = day.boxes && day.boxes[0];
    if (box && _oipCprSubChecked('oipCprShowBand')) {
        const n = _oipVisibleCount(day.times, maxTime);
        const s = _oipCprBoxSeries(idx, st.fill || _oipCprFade(oipGetLineColor('cprBand'), 0.14));
        try {
            if (created) {
                s.applyOptions({ baseValue: { type: 'price', price: box.min } });
                s.setData(day.times.slice(0, n).map(t => ({ time: t, value: box.max })));
            } else {
                s.update({ time: maxTime, value: box.max });
            }
        } catch (e) { return false; }
        st.boxCount[idx] = n;
    }

    st.liveIdx = idx;
    st.maxTime = maxTime;
    if (created) oipApplyZOrder();
    return true;
}

function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;
    if (!oipElems.showCpr?.checked || !candles || !candles.length) { _oipCprBlank(); return; }
    oipRenderCprLevels(oipCalculateDynamicCPR(candles));
}

/* ── Multi CPR ────────────────────────────────────────────── */
function _oipAggregateToNMin(candles, minutes) {
    const MARKET_OPEN = 9 * 60 + 15;
    const buckets = new Map();
    candles.forEach(c => {
        const d     = new Date(c.time * 1000);
        const total = d.getUTCHours() * 60 + d.getUTCMinutes();
        const idx   = Math.floor((total - MARKET_OPEN) / minutes);
        const bMin  = MARKET_OPEN + idx * minutes;
        const bd    = new Date(d);
        bd.setUTCHours(Math.floor(bMin / 60), bMin % 60, 0, 0);
        const key   = Math.floor(bd.getTime() / 1000);
        if (!buckets.has(key)) {
            buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close, times: [c.time] });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
            b.times.push(c.time);
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

function oipDrawMultiCPR(candles) {
    if (!oipOIChart || !oipOISeries) return;

    Object.values(oipMultiCprSeriesMap).forEach(s => { try { s.setData([]); } catch(e) {} });

    const show = document.getElementById('oipShowMultiCpr')?.checked;
    if (!show || !candles?.length) return;

    const shared = {
        lastValueVisible: false, priceLineVisible: false,
        crosshairMarkerVisible: false, autoscaleInfoProvider: () => null
    };

    const configs = [
        { id: 'oipMultiCpr15m', styleKey: 'multiCpr15m', minutes: 15, color: '#f97316', fill: 'rgba(249,115,22,0.07)'  },
        { id: 'oipMultiCpr30m', styleKey: 'multiCpr30m', minutes: 30, color: '#06b6d4', fill: 'rgba(6,182,212,0.07)'   },
        { id: 'oipMultiCpr1h',  styleKey: 'multiCpr1h',  minutes: 60, color: '#9c28b0', fill: 'rgba(235, 212, 239, 0.5)'  }  // #ebd4ef @ 50%
    ];

    // Build per-config CONTINUOUS line data (one stepped line spanning all
    // buckets) plus per-bucket fill segments (BaselineSeries needs a per-bucket
    // baseValue, so the band fill stays segmented while the lines run continuous).
    const fillSegs  = [];
    const lineSpecs = [];
    configs.forEach(({ id, styleKey, minutes, color, fill }) => {
        const enabled = document.getElementById(id)?.checked !== false;
        const bars    = _oipAggregateToNMin(candles, minutes);
        const ppData = [], bcData = [], tcData = [];
        for (let i = 1; i < bars.length; i++) {
            const prev = bars[i - 1], curr = bars[i];
            if (!curr.times.length) continue;
            const oH = prev.high, oL = prev.low, oC = prev.close;
            const pp = (oH + oL + oC) / 3;
            const bc = (oH + oL) / 2;
            const tc = 2 * pp - bc;
            // Append this bucket's times to the continuous line (steps at the boundary).
            for (const t of curr.times) {
                ppData.push({ time: t, value: pp });
                bcData.push({ time: t, value: bc });
                tcData.push({ time: t, value: tc });
            }
            fillSegs.push({ fill, enabled, times: curr.times, tc, bc, fillKey: `mc_fill_${minutes}_${i}` });
        }
        lineSpecs.push({
            color, enabled, ppData, bcData, tcData, styleKey,
            tcKey: `mc_tc_${minutes}`, ppKey: `mc_pp_${minutes}`, bcKey: `mc_bc_${minutes}`
        });
    });

    // Pass 1 — create + set ALL fills first (lowest in the z-stack).
    fillSegs.forEach(s => {
        if (!oipMultiCprSeriesMap[s.fillKey]) {
            oipMultiCprSeriesMap[s.fillKey] = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
                baseValue: { type: 'price', price: Math.min(s.tc, s.bc) },
                topFillColor1: s.fill, topFillColor2: s.fill, topLineColor: 'transparent',
                bottomFillColor1: s.fill, bottomFillColor2: s.fill, bottomLineColor: 'transparent',
                lineWidth: 0, ...shared
            });
        }
        if (s.enabled) {
            oipMultiCprSeriesMap[s.fillKey].applyOptions({ baseValue: { type: 'price', price: Math.min(s.tc, s.bc) } });
            oipMultiCprSeriesMap[s.fillKey].setData(s.times.map(t => ({ time: t, value: Math.max(s.tc, s.bc) })));
        } else {
            oipMultiCprSeriesMap[s.fillKey].setData([]);
        }
    });

    // Pass 2 — one continuous PP/BC/TC line per config, drawn above every fill.
    lineSpecs.forEach(s => {
        const style = oipGetLineStyle(s.styleKey);
        const col   = oipGetLineColor(s.styleKey);
        const wid   = oipGetLineWidth(s.styleKey);
        if (!oipMultiCprSeriesMap[s.tcKey]) {
            oipMultiCprSeriesMap[s.tcKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: col, lineWidth: wid, lineStyle: style, ...shared });
        }
        if (!oipMultiCprSeriesMap[s.ppKey]) {
            oipMultiCprSeriesMap[s.ppKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: col, lineWidth: wid, lineStyle: style, ...shared });
        }
        if (!oipMultiCprSeriesMap[s.bcKey]) {
            oipMultiCprSeriesMap[s.bcKey] = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: col, lineWidth: wid, lineStyle: style, ...shared });
        }
        // Series are cached and reused across redraws — always re-apply the
        // current color/width/style choice, not just at first creation.
        oipMultiCprSeriesMap[s.tcKey].applyOptions({ lineStyle: style, color: col, lineWidth: wid });
        oipMultiCprSeriesMap[s.ppKey].applyOptions({ lineStyle: style, color: col, lineWidth: wid });
        oipMultiCprSeriesMap[s.bcKey].applyOptions({ lineStyle: style, color: col, lineWidth: wid });
        oipMultiCprSeriesMap[s.tcKey].setData(s.enabled ? s.tcData : []);
        oipMultiCprSeriesMap[s.ppKey].setData(s.enabled ? s.ppData : []);
        oipMultiCprSeriesMap[s.bcKey].setData(s.enabled ? s.bcData : []);
    });
    oipApplyZOrder();
}

/* ── Global z-order policy (main OI pane) ─────────────────────
 * Enforces a single, deterministic stack so no indicator is hidden behind
 * another regardless of which order indicators were toggled on:
 *     band/box FILLS  (bottom)  →  all LINES  →  candles  (top)
 * Uses v5 ISeriesApi.setSeriesOrder with contiguous indices. Safe no-op on v4.
 */
function oipApplyZOrder() {
    if (!oipOISeries || typeof oipOISeries.setSeriesOrder !== 'function') return;

    const fills = [];
    const lines = [];

    // Future-sourced volume histograms sit at the very bottom of the stack.
    // Nifty first so Banknifty stays on top of it, matching the order they were
    // created in (see oipAddVolumeSeriesPair).
    if (typeof oipVolumeSeries !== 'undefined' && oipVolumeSeries) fills.push(oipVolumeSeries);
    if (typeof oipBnfVolumeSeries !== 'undefined' && oipBnfVolumeSeries) fills.push(oipBnfVolumeSeries);

    // CPR levels: box_* are fills, line_* are lines.
    Object.keys(oipCprSeriesMap).forEach(k => {
        const s = oipCprSeriesMap[k];
        if (s) (k.startsWith('box_') ? fills : lines).push(s);
    });
    // Multi-CPR: mc_fill_* are fills, mc_tc/pp/bc_* are lines.
    Object.keys(oipMultiCprSeriesMap).forEach(k => {
        const s = oipMultiCprSeriesMap[k];
        if (s) (k.startsWith('mc_fill_') ? fills : lines).push(s);
    });
    // EMAs + VWAP variants + max-pain (all lines on the main pane).
    [oipEma9Series, oipEma20Series, oipEma50Series, oipEma100Series, oipEma200Series,
     typeof oipVwapSeries     !== 'undefined' ? oipVwapSeries     : null,
     typeof oipCvwapSeries    !== 'undefined' ? oipCvwapSeries    : null,
     typeof oipPvwapSeries    !== 'undefined' ? oipPvwapSeries    : null,
     typeof oipAvg3VwapSeries !== 'undefined' ? oipAvg3VwapSeries : null,
     typeof oipMaxPainSeries  !== 'undefined' ? oipMaxPainSeries  : null
    ].forEach(s => { if (s) lines.push(s); });

    // RSI S&R lines (drawn on the price pane).
    if (oipRSISeriesObj) Object.values(oipRSISeriesObj).forEach(s => { if (s) lines.push(s); });

    // Candle boxes (main pane only): fill → fills, top/bottom borders → lines.
    const pushBoxes = arr => (arr || []).forEach(b => {
        if (!b || b.chart !== oipOIChart) return;
        if (b.fill) fills.push(b.fill);
        if (b.top) lines.push(b.top);
        if (b.bottom) lines.push(b.bottom);
    });
    if (typeof oip2ndCandle30sBox !== 'undefined') pushBoxes(oip2ndCandle30sBox.oi);
    if (typeof oip2nd5mCandleBox  !== 'undefined') pushBoxes(oip2nd5mCandleBox.oi);
    if (typeof oipMondayBoxes     !== 'undefined') pushBoxes(oipMondayBoxes);

    // Reversal lines (kept above fills).
    (oip30mReversalSeries || []).forEach(s => { if (s) lines.push(s); });
    if (typeof oip1DReversalSeries !== 'undefined')
        (oip1DReversalSeries || []).forEach(s => { if (s) lines.push(s); });

    let order = 0;
    fills.forEach(s => { try { s.setSeriesOrder(order++); } catch (e) {} });
    lines.forEach(s => { try { s.setSeriesOrder(order++); } catch (e) {} });
    try { oipOISeries.setSeriesOrder(order++); } catch (e) {}  // candles on top

    oipApplyOptionZOrder();
}

// Layer a single option-premium pane: box FILLS (bottom) → EMA/VWAP/box-border
// LINES → candle(s) on top. Used for the CE, PE and combined premium charts.
function _oipLayerPane(candleSeries, lineSeries, boxArrays) {
    const fills = [];
    const lines = [];
    lineSeries.forEach(s => { if (s) lines.push(s); });
    boxArrays.forEach(arr => (arr || []).forEach(b => {
        if (!b) return;
        if (b.fill) fills.push(b.fill);
        if (b.top) lines.push(b.top);
        if (b.bottom) lines.push(b.bottom);
    }));
    let order = 0;
    const set = s => { if (s && typeof s.setSeriesOrder === 'function') { try { s.setSeriesOrder(order++); } catch (e) {} } };
    fills.forEach(set);
    lines.forEach(set);
    candleSeries.forEach(set); // candles on top of their own pane
}

// Keep candles on top across the CE / PE / combined premium panes.
function oipApplyOptionZOrder() {
    const box30 = typeof oip2ndCandle30sBox !== 'undefined' ? oip2ndCandle30sBox : { ce: [], pe: [] };
    const box5m = typeof oip2nd5mCandleBox  !== 'undefined' ? oip2nd5mCandleBox  : { ce: [], pe: [] };

    _oipLayerPane(
        [oipCESeries],
        [oipCEEma9Series, oipCEEma20Series, oipCEEma50Series, oipCECvwapSeries, oipCEPvwapSeries],
        [box30.ce, box5m.ce]
    );
    _oipLayerPane(
        [oipPESeries],
        [oipPEEma9Series, oipPEEma20Series, oipPEEma50Series, oipPECvwapSeries, oipPEPvwapSeries],
        [box30.pe, box5m.pe]
    );
    _oipLayerPane(
        [oipIntrinsicSeries, oipIntrinsicPeSeries],
        [oipVwapIntSeries, oipVwapIntPeSeries, oipCvwapIntSeries, oipCvwapIntPeSeries, oipPvwapIntSeries, oipPvwapIntPeSeries],
        []
    );
    _oipLayerPane(
        [typeof oipFixedCeSeries !== 'undefined' ? oipFixedCeSeries : null,
         typeof oipFixedPeSeries !== 'undefined' ? oipFixedPeSeries : null],
        [typeof oipFixedCeHL2Series    !== 'undefined' ? oipFixedCeHL2Series    : null,
         typeof oipFixedPeHL2Series    !== 'undefined' ? oipFixedPeHL2Series    : null,
         typeof oipFixedCloseAvgSeries !== 'undefined' ? oipFixedCloseAvgSeries : null],
        []
    );
}

/* ── Indicators popup ─────────────────────────────────────── */
function oipInitIndicatorsPopup(storageKey) {
    // Wire showEma200 (was declared in oipElems but never initialized)
    oipElems.showEma200 = document.getElementById('oipShowEma200');

    // Restore persisted state before anything is drawn
    if (storageKey) _oipRestoreIndicators(storageKey);

    // Inject the per-line Solid/Dotted/Dashed selectors into every indicator
    // checkbox row (main popup + Opt Indicator popup + Synthetic sub-row).
    oipInjectLineStyleSelectors();

    const btn   = document.getElementById('oipIndicatorsBtn');
    const popup = document.getElementById('oipIndicatorsPopup');

    if (btn && popup) {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            popup.classList.toggle('hidden');
        });
        document.addEventListener('click', e => {
            if (!popup.contains(e.target) && e.target !== btn) {
                popup.classList.add('hidden');
            }
        });
        // Save whenever any checkbox inside the popup changes
        if (storageKey) popup.addEventListener('change', () => _oipSaveIndicators(storageKey));
    } else if (storageKey) {
        // No popup (compact toolbar page): delegate on document for the known IDs
        const indSet = new Set(_OIP_IND_IDS);
        document.addEventListener('change', e => {
            if (e.target.type === 'checkbox' && indSet.has(e.target.id)) {
                _oipSaveIndicators(storageKey);
            }
        }, true);
    }

    // ── "Opt Indicator" popup — a separate, smaller popup for the option
    // (CE-only/PE-only) charts: Synthetic value (Diff/PDC/C-P) + proxy
    // checkboxes for indicators that also apply to the option charts.
    const optBtn   = document.getElementById('oipOptIndicatorsBtn');
    const optPopup = document.getElementById('oipOptIndicatorsPopup');

    if (optBtn && optPopup) {
        optBtn.addEventListener('click', e => {
            e.stopPropagation();
            optPopup.classList.toggle('hidden');
        });
        document.addEventListener('click', e => {
            if (!optPopup.contains(e.target) && e.target !== optBtn && !optBtn.contains(e.target)) {
                optPopup.classList.add('hidden');
            }
        });
        // Save whenever any checkbox inside this popup changes (proxy checkboxes
        // aren't in _OIP_IND_IDS — only their mirrored main-popup ids are — so
        // this only actually persists oipShowSynthetic).
        if (storageKey) optPopup.addEventListener('change', () => _oipSaveIndicators(storageKey));
    }

    // Synthetic value (Diff / PDC / C-P) — drawn by oipDrawPremStrikeLines(),
    // already gated on strikeMode === 'atm'; this checkbox adds an explicit
    // on/off switch on top of that.
    document.getElementById('oipShowSynthetic')?.addEventListener('change', () => {
        if (typeof oipDrawPremStrikeLines === 'function') oipDrawPremStrikeLines();
    });

    // Option-chart-only checkboxes — INDEPENDENT from their main-popup
    // namesakes: the main popup's checkbox controls only the main (and, for
    // VWAP, Options Premium) chart; these control only the CE Only / PE Only
    // charts. Each has its own persisted state via _OIP_IND_IDS.
    document.getElementById('oipShow2ndCandle30sOpt')?.addEventListener('change', () => {
        if (oipOIData?.candles) oipDraw2ndCandle30sBox(oipOIData.candles);
    });
    document.getElementById('oipShow2nd5mCandleOpt')?.addEventListener('change', () => {
        if (oipOIData?.candles) oipDraw2nd5mCandleBox(oipOIData.candles);
    });
    document.getElementById('oipShowVwapOpt')?.addEventListener('change', () => oipSyncVwapVisibility());
    // 5m Close Border — one instance per chart group, each with its own toggle
    // and colour (main popup drives the OI chart, opt popup the CE/PE/Combined
    // premium charts).
    document.getElementById('oipShow5mClose')?.addEventListener('change', () => oipRedraw5mCloseMain());
    document.getElementById('oipShowOpt5mClose')?.addEventListener('change', () => oipRedraw5mCloseOpt());
    ['oipShowVolumeOpt', 'oipShowBnfVolumeOpt'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (typeof oipSyncOptVolumeVisibility === 'function') oipSyncOptVolumeVisibility();
        });
    });

    // Fixed 24000-strike chart's own reference lines — each has its own checkbox.
    ['oipShowFixedCeAvg', 'oipShowFixedPeAvg', 'oipShowFixedCePeAvg'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', () => {
            if (typeof oipSyncFixedChartVisibility === 'function') oipSyncFixedChartVisibility();
        });
    });

    // CPR expand / collapse
    const cprExpandBtn = document.getElementById('oipCprExpandBtn');
    const cprSub       = document.getElementById('oipCprSub');
    const cprMaster    = document.getElementById('oipShowCpr');

    if (cprExpandBtn && cprSub) {
        cprExpandBtn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = cprSub.classList.toggle('hidden');
            cprExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }

    function _syncCprSubState() {
        if (!cprSub || !cprMaster) return;
        cprSub.classList.toggle('oip-cpr-disabled', !cprMaster.checked);
    }
    if (cprMaster) {
        cprMaster.addEventListener('change', _syncCprSubState);
        _syncCprSubState();
    }

    // Multi CPR expand / collapse
    const multiCprExpandBtn = document.getElementById('oipMultiCprExpandBtn');
    const multiCprSub       = document.getElementById('oipMultiCprSub');
    const multiCprMaster    = document.getElementById('oipShowMultiCpr');

    if (multiCprExpandBtn && multiCprSub) {
        multiCprExpandBtn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = multiCprSub.classList.toggle('hidden');
            multiCprExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }

    function _syncMultiCprSubState() {
        if (!multiCprSub || !multiCprMaster) return;
        multiCprSub.classList.toggle('oip-cpr-disabled', !multiCprMaster.checked);
    }
    if (multiCprMaster) {
        multiCprMaster.addEventListener('change', _syncMultiCprSubState);
        _syncMultiCprSubState();
    }

    // VWAP group (CVWAP / PVWAP / 3-AVG_VWAP) expand / collapse
    const vwapExpandBtn = document.getElementById('oipVwapExpandBtn');
    const vwapSub       = document.getElementById('oipVwapSub');
    const vwapMaster     = document.getElementById('oipShowVwapGroup');

    if (vwapExpandBtn && vwapSub) {
        vwapExpandBtn.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = vwapSub.classList.toggle('hidden');
            vwapExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }

    function _syncVwapSubState() {
        if (!vwapSub || !vwapMaster) return;
        vwapSub.classList.toggle('oip-cpr-disabled', !vwapMaster.checked);
    }
    if (vwapMaster) {
        vwapMaster.addEventListener('change', _syncVwapSubState);
        _syncVwapSubState();
    }

    // Moving Averages (EMA) expand / collapse
    const emaHeader    = document.getElementById('oipEmaHeader');
    const emaExpandBtn = document.getElementById('oipEmaExpandBtn');
    const emaSub       = document.getElementById('oipEmaSub');
    if (emaHeader && emaSub) {
        emaHeader.addEventListener('click', e => {
            e.stopPropagation();
            e.preventDefault();
            const isNowHidden = emaSub.classList.toggle('hidden');
            if (emaExpandBtn) emaExpandBtn.classList.toggle('expanded', !isNowHidden);
        });
    }
}

/* ── 9:18 ATM CE OI Lines ─────────────────────────────────── */
// Selected at 09:18: the ATM strike plus the adjacent strike chosen by CE OI.
// Drawn as horizontal price lines on the main (underlying) candle series.
let oipAtmCeOiData  = null;   // cached result from /api/oi-profile/atm-ce-oi-strikes
let oipAtmCeOiLines = [];     // price-line handles on oipOISeries

function oipClearAtmCeOiLines() {
    oipAtmCeOiLines.forEach(l => { try { oipOISeries?.removePriceLine(l); } catch (e) {} });
    oipAtmCeOiLines = [];
}

// Fetch + cache the 09:18 ATM CE OI strike selection. Always runs so the data
// is "kept ready" regardless of the checkbox; drawing is gated separately.
// NIFTY only. `dateStr` (YYYY-MM-DD) is passed in replay mode; omit for today.
async function oipFetchAtmCeOiStrikes(symbol, step, dateStr) {
    oipAtmCeOiData = null;
    if ((symbol || '').toUpperCase() !== 'NIFTY') return;
    try {
        let url = `/api/oi-profile/atm-ce-oi-strikes?symbol=${encodeURIComponent(symbol)}&step=${step || 50}`;
        if (dateStr) url += `&date=${dateStr}`;
        const res  = await fetch(url);
        const data = await res.json();
        if (data && data.success) oipAtmCeOiData = data;
        else console.warn('[OIP] ATM CE OI:', data && data.error);
    } catch (e) {
        console.warn('[OIP] ATM CE OI fetch failed:', e);
    }
}

// Draw the two cached strike levels as horizontal price lines. Cheap to call
// repeatedly (clears + recreates 2 lines); gated by the checkbox.
function oipDrawAtmCeOiLines() {
    oipClearAtmCeOiLines();
    if (!oipOISeries) return;
    if (!document.getElementById('oipShowAtmCeOi')?.checked) return;

    const d = oipAtmCeOiData;
    if (!d || !Array.isArray(d.selected)) return;

    d.selected.forEach((s) => {
        if (s.strike == null) return;
        oipAtmCeOiLines.push(oipOISeries.createPriceLine({
            price: s.strike,
            color: oipGetLineColor('atmCeOi'),
            lineWidth: oipGetLineWidth('atmCeOi'),
            lineStyle: oipGetLineStyle('atmCeOi'), // dashed by default
            axisLabelVisible: false,
            title: ''
        }));
    });
}

/* ── 30-min Reversal Lines ────────────────────────────────── */
let oip30mReversalSeries = [];

function oipClear30mReversalLines() {
    oip30mReversalSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oip30mReversalSeries = [];
}

// Aggregate arbitrary-interval candles into 30-minute OHLC buckets aligned to
// Indian market open (9:15 AM IST).  Buckets: 9:15, 9:45, 10:15, 10:45 …
// Candles use Fake-IST encoding: UTC hours/minutes == IST hours/minutes.
function _oipAggregateTo30m(candles) {
    const MARKET_OPEN = 9 * 60 + 15; // 555 minutes from midnight IST
    const buckets = new Map();

    candles.forEach(c => {
        const d      = new Date(c.time * 1000);
        const total  = d.getUTCHours() * 60 + d.getUTCMinutes();
        const offset = total - MARKET_OPEN;               // mins since 9:15
        const idx    = Math.floor(offset / 30);           // which 30-min slot
        const bMin   = MARKET_OPEN + idx * 30;            // slot start in mins

        const bd = new Date(d);
        bd.setUTCHours(Math.floor(bMin / 60), bMin % 60, 0, 0);
        const key = Math.floor(bd.getTime() / 1000);

        if (!buckets.has(key)) {
            buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

// Generate future timestamps that stay within 9:15 AM – 3:30 PM IST trading sessions.
// Uses Fake-IST encoding: UTC hours/minutes == IST hours/minutes.
function _oipFutureSessionTimes(fromTime, intervalSecs, count) {
    const result = [];
    let t = fromTime;
    const SESSION_START = 9 * 60 + 15;   // 555 min
    const SESSION_END   = 15 * 60 + 30;  // 930 min

    function jumpToNextSession(ts) {
        const d = new Date(ts * 1000);
        d.setUTCDate(d.getUTCDate() + 1);
        while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() + 1);
        d.setUTCHours(9, 15, 0, 0);
        return Math.floor(d.getTime() / 1000);
    }

    while (result.length < count) {
        t += intervalSecs;
        const d   = new Date(t * 1000);
        const dow = d.getUTCDay();
        const min = d.getUTCHours() * 60 + d.getUTCMinutes();
        if (dow === 0 || dow === 6 || min < SESSION_START || min > SESSION_END) {
            t = jumpToNextSession(t);
        }
        result.push(t);
    }
    return result;
}

const _OIP_30M_ALLOWED = new Set([
    '30second', 'minute', '2minute', '3minute', '5minute',
    '10minute', '15minute', '30minute'
]);

// Cached deduped reversal levels from the last full signal detection run.
// Only updated when recompute=true (i.e., on 30m candle close during replay, or always in live mode).
let _oip30mLevelCache = null;

// recompute=true (default): detect new signals and update cache — use on 30m close or live mode.
// recompute=false: redraw existing cached levels with updated time endpoints — use mid-bar in replay.
function oipDraw30mReversalLines(candles, recompute = true) {
    oipClear30mReversalLines();
    if (!document.getElementById('oipShow30mReversalLines')?.checked) return;
    // Only valid for 30-min and below; silent no-op for 60m / daily / weekly
    if (typeof oipInterval !== 'undefined' && !_OIP_30M_ALLOWED.has(oipInterval)) return;
    if (!oipOIChart || !oipOISeries || !candles?.length) return;

    const lastTime = candles[candles.length - 1].time;
    const curPrice = candles[candles.length - 1].close;

    // ── Robust interval: mode of last 30 consecutive intra-session gaps ──
    const sample = candles.slice(-31);
    const freq = {};
    for (let i = 1; i < sample.length; i++) {
        const d = sample[i].time - sample[i - 1].time;
        if (d > 0 && d <= 3600) freq[d] = (freq[d] || 0) + 1; // ignore overnight gaps
    }
    const candleInterval = Object.keys(freq).length
        ? parseInt(Object.keys(freq).reduce((a, b) => freq[a] > freq[b] ? a : b))
        : 60;

    // Read user-configured values
    const lineCountUp = Math.max(1, parseInt(document.getElementById('oipReversal30mCountUp')?.value) || 10);
    const lineCountDn = Math.max(1, parseInt(document.getElementById('oipReversal30mCountDn')?.value) || 10);
    const lookback    = Math.max(5, parseInt(document.getElementById('oipReversal30mRange')?.value)  || 50);

    // In replay mode skip future timestamps — they expand the time scale and crash the renderer.
    const FUTURE_BARS = 50;
    const isReplay = typeof oipFullCandles !== 'undefined' && oipFullCandles && candles.length < oipFullCandles.length;
    const futureTimes = isReplay ? [] : _oipFutureSessionTimes(lastTime, candleInterval, FUTURE_BARS);
    window._oipReversalFutureBarsCount = futureTimes.length;

    // ── Signal detection: only runs on 30m close (recompute=true) or first call ──
    if (recompute || _oip30mLevelCache === null) {
        const bars = _oipAggregateTo30m(candles);
        if (bars.length < 2) { _oip30mLevelCache = []; }
        else {
            const GAP_PTS = 10;
            const barsInRange = bars.slice(-lookback);
            const rawLevels = [];
            for (let i = 0; i < barsInRange.length - 1; i++) {
                const c1 = barsInRange[i];
                const c2 = barsInRange[i + 1];
                const c1Bull = c1.close > c1.open;
                const c2Bull = c2.close > c2.open;
                if (c1Bull === c2Bull) continue;
                if (Math.abs(c1.close - c2.open) > GAP_PTS) continue;
                const isBullish = !c1Bull && c2Bull;
                const next2 = barsInRange.slice(i + 2, i + 4);
                if (!next2.length) continue;
                let breakOccurred, closeConfirmed;
                if (isBullish) {
                    breakOccurred  = next2.some(b => b.high  > c2.high);
                    closeConfirmed = next2.some(b => b.close > c2.high);
                } else {
                    breakOccurred  = next2.some(b => b.low   < c2.low);
                    closeConfirmed = next2.some(b => b.close < c2.low);
                }
                if (!breakOccurred || !closeConfirmed) continue;
                rawLevels.push({ level: (c1.close + c2.open) / 2, time: c1.time });
            }
            rawLevels.sort((a, b) => a.level - b.level);
            const deduped = [];
            for (const l of rawLevels) {
                const prev = deduped[deduped.length - 1];
                if (prev && Math.abs(l.level - prev.level) <= 5) {
                    if (l.time > prev.time) deduped[deduped.length - 1] = l;
                } else {
                    deduped.push(l);
                }
            }
            _oip30mLevelCache = deduped;
        }
    }

    const deduped = _oip30mLevelCache;
    if (!deduped?.length) return;

    // ── Pick closest N above/below current price (recomputed every step so lines follow price) ──
    const above = deduped.filter(l => l.level >= curPrice)
                         .sort((a, b) => a.level - b.level)
                         .slice(0, lineCountUp);
    const below = deduped.filter(l => l.level < curPrice)
                         .sort((a, b) => b.level - a.level)
                         .slice(0, lineCountDn);

    // ── Draw lines extended to the current candle's time ────────
    [...above, ...below].forEach(({ level, time }) => {
        const historical = candles
            .filter(c => c.time >= time)
            .map(c => ({ time: c.time, value: level }));
        const future = futureTimes.map(t => ({ time: t, value: level }));
        const s = oipOIChart.addSeries(LightweightCharts.LineSeries, {
            color: oipGetLineColor('reversal30m'),
            lineWidth: oipGetLineWidth('reversal30m'),
            lineStyle: oipGetLineStyle('reversal30m'),
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => null,
        });
        s.setData([...historical, ...future]);
        oip30mReversalSeries.push(s);
    });
    oipApplyZOrder();
}

/* ── 1-Day Reversal Lines ─────────────────────────────────── */

// Aggregate any-interval candles into 1-Day OHLC bars.
// Each day's canonical time = 9:15 AM IST (Fake-IST encoding).
function _oipAggregateTo1D(candles) {
    const buckets = new Map();
    candles.forEach(c => {
        const d   = new Date(c.time * 1000);
        const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        if (!buckets.has(key)) {
            const bd = new Date(d);
            bd.setUTCHours(9, 15, 0, 0);
            buckets.set(key, { time: Math.floor(bd.getTime()/1000), open: c.open, high: c.high, low: c.low, close: c.close });
        } else {
            const b = buckets.get(key);
            b.high  = Math.max(b.high, c.high);
            b.low   = Math.min(b.low,  c.low);
            b.close = c.close;
        }
    });
    return [...buckets.values()].sort((a, b) => a.time - b.time);
}

// Generate N future trading-day timestamps (9:15 AM IST, skipping weekends).
function _oipFutureTradingDays(fromTime, count) {
    const result = [];
    const d = new Date(fromTime * 1000);
    while (result.length < count) {
        d.setUTCDate(d.getUTCDate() + 1);
        if (d.getUTCDay() === 0 || d.getUTCDay() === 6) continue;
        d.setUTCHours(9, 15, 0, 0);
        result.push(Math.floor(d.getTime() / 1000));
    }
    return result;
}

let oip1DReversalSeries = [];

function oipClear1DReversalLines() {
    oip1DReversalSeries.forEach(s => { try { oipOIChart.removeSeries(s); } catch(e) {} });
    oip1DReversalSeries = [];
}

function oipDraw1DReversalLines(candles) {
    oipClear1DReversalLines();
    if (!document.getElementById('oipShow1DReversalLines')?.checked) return;
    if (!oipOIChart || !oipOISeries || !candles?.length) return;

    const bars = _oipAggregateTo1D(candles);
    if (bars.length < 2) return;

    const count = Math.max(1, parseInt(document.getElementById('oipReversal1DCount')?.value) || 10);
    const range = Math.max(1, parseInt(document.getElementById('oipReversal1DRange')?.value) || 30);

    // Skip future timestamps in replay mode — same reason as oipDraw30mReversalLines.
    const FUTURE_DAYS = 30;
    const isReplay1D = typeof oipFullCandles !== 'undefined' && oipFullCandles && candles.length < oipFullCandles.length;
    const futureTimes = isReplay1D ? [] : _oipFutureTradingDays(bars[bars.length - 1].time, FUTURE_DAYS);
    window._oipReversalFutureBarsCount = futureTimes.length;

    const shared = {
        lastValueVisible: false, priceLineVisible: false,
        crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
    };

    // ── 1. Scan backwards (skip last incomplete bar) for candles
    //       where |close - open| <= range ──────────────────────────
    const qualifying = [];
    for (let i = bars.length - 2; i >= 0 && qualifying.length < count; i--) {
        const b = bars[i];
        if (Math.abs(b.close - b.open) <= range) qualifying.push(b);
    }

    if (!qualifying.length) return;

    // ── 2. For each qualifying candle draw a body-zone box + center line ──
    qualifying.forEach(b => {
        const top    = Math.max(b.open, b.close);
        const bottom = Math.min(b.open, b.close);
        const center = (b.open + b.close) / 2;
        const isBull = b.close >= b.open;
        const lineColor = isBull ? '#22c55e' : '#ef4444';
        const fillColor = isBull ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

        // Timeline: from this candle forward through all remaining daily bars + future days
        const allTimes = [
            ...bars.filter(bar => bar.time >= b.time).map(bar => bar.time),
            ...futureTimes,
        ];

        // Top border
        const topS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: oipGetLineStyle('reversal1d'), ...shared });
        topS.setData(allTimes.map(t => ({ time: t, value: top })));
        oip1DReversalSeries.push(topS);

        // Bottom border
        const botS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: oipGetLineStyle('reversal1d'), ...shared });
        botS.setData(allTimes.map(t => ({ time: t, value: bottom })));
        oip1DReversalSeries.push(botS);

        // Fill between top and bottom
        const fillS = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
            baseValue: { type: 'price', price: bottom },
            topFillColor1: fillColor, topFillColor2: fillColor, topLineColor: 'transparent',
            bottomFillColor1: 'transparent', bottomFillColor2: 'transparent', bottomLineColor: 'transparent',
            lineWidth: 0, ...shared,
        });
        fillS.setData(allTimes.map(t => ({ time: t, value: top })));
        oip1DReversalSeries.push(fillS);

        // Center line
        const cenS = oipOIChart.addSeries(LightweightCharts.LineSeries, { color: lineColor, lineWidth: 1, lineStyle: oipGetLineStyle('reversal1d'), ...shared });
        cenS.setData(allTimes.map(t => ({ time: t, value: center })));
        oip1DReversalSeries.push(cenS);
    });
    oipApplyZOrder();
}
