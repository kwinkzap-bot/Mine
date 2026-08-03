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
const _OIP_LINE_STYLE_DEFAULTS = { maxPain: 2, atmCeOi: 2 };
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
    cprPrevHL: { color: '#ef07f9', width: 1 }, cprBand: { color: '#00008B', width: 1 },
    cprResistance: { color: '#006400', width: 1 }, cprSupport: { color: '#ff0000', width: 1 }, cprCumR3S3: { color: '#a020f0', width: 2 },
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
    // Banknifty's overlay defaults to the PE chart's candle colours (violet up,
    // dark down), which also keeps it clear of the green/red pair above — the
    // two histograms share a price scale and overlap. PE's down colour is
    // theme-dependent, so this default is a function: it re-resolves per read,
    // and a user-picked colour still overrides it outright.
    bnfVolUp: { color: '#8b5cf6' }, bnfVolDn: { color: () => _oipPeDownColor() },
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
    nifty:     ['volUp', 'volDn'],
    banknifty: ['bnfVolUp', 'bnfVolDn'],
};
const _OIP_VOL_COLOR_KEY_SET = new Set(Object.values(_OIP_VOL_COLOR_KEYS).flat());
function oipVolumeBarColors(kind) {
    const [upKey, dnKey] = _OIP_VOL_COLOR_KEYS[kind] || _OIP_VOL_COLOR_KEYS.nifty;
    return { up: oipGetLineColor(upKey) + _OIP_VOL_BAR_ALPHA,
             down: oipGetLineColor(dnKey) + _OIP_VOL_BAR_ALPHA };
}

// Creates one chart's pair of volume histograms. Both sit on the SAME hidden
// price scale, pinned to the bottom 20% of the pane, so the two are comparable
// by bar height and neither competes with candle prices. Banknifty is added
// second so it draws on top of Nifty; at 50% alpha each, the overlap reads as a
// blend rather than one hiding the other. `chart` is the raw LightweightCharts
// object (i.e. `X.chart` for TradingViewChart wrappers). Returns
// [niftySeries, banknNiftySeries].
function oipAddVolumeSeriesPair(chart, priceScaleId, showNifty = true, showBnf = false) {
    const base = {
        priceFormat: { type: 'volume' },
        priceScaleId,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
    };
    const nifty = chart.addSeries(LightweightCharts.HistogramSeries, { ...base, visible: showNifty });
    const bnf   = chart.addSeries(LightweightCharts.HistogramSeries, { ...base, visible: showBnf });
    chart.priceScale(priceScaleId).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
    return [nifty, bnf];
}

// series -> {kind, bars:[{time, value, up}]} last pushed to it. Direction is
// baked into a histogram point's `color`, so without this cache a colour
// change couldn't tell an up bar from a down one after the fact — and
// refetching candles just to repaint would be wasteful. `kind` rides along so
// a repaint knows which colour pair the series belongs to.
const _oipVolBarCache = new Map();

function _oipPaintVolumeBars(series, kind, bars) {
    const { up, down } = oipVolumeBarColors(kind);
    try {
        series.setData(bars.map(b => ({ time: b.time, value: b.value, color: b.up ? up : down })));
    } catch (e) {}
}

// Builds and pushes one chart's volume bars. `futureVolume` is the backend's
// array (future_volume / banknifty_volume / their fixed_* twins); `refCandles`
// are the candles actually drawn on that chart — they share the same
// interval-driven time-bucket grid, so matching by exact time key is safe.
// Bars are emitted only where refCandles has a REAL (non-whitespace) candle,
// so gaps don't render a misleading zero-volume bar. `kind` picks the colour
// pair — see _OIP_VOL_COLOR_KEYS.
function oipSetVolumeBars(series, futureVolume, refCandles, kind = 'nifty') {
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
    _oipVolBarCache.set(series, { kind, bars });
    _oipPaintVolumeBars(series, kind, bars);
}

// Re-tints every already-drawn histogram in place — no refetch, no redraw of
// anything else. Called when any of the four volume colour pickers changes;
// each series repaints from its own cached `kind`, so only the overlay that
// actually changed ends up looking different.
function oipRepaintAllVolumeBars() {
    _oipVolBarCache.forEach(({ kind, bars }, series) => _oipPaintVolumeBars(series, kind, bars));
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
const _OIP_5M_CLOSE_BAR_SECONDS = { '30second': 30, 'minute': 60 };

function oipMark5mCloseBorders(candles, enabled, color) {
    if (!Array.isArray(candles) || !enabled) return candles;
    const barSec = _OIP_5M_CLOSE_BAR_SECONDS[oipInterval];
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

function oipCalculateVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    let cumPV = 0, cumV = 0, lastDate = null;
    const result = [];
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        // Use UTC methods to match the 'Fake IST Epoch' from server
        const date = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
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

// CVWAP — alias for the current-session VWAP (resets each trading day).
// Kept as a thin wrapper so the indicator wiring reads CVWAP/PVWAP symmetrically.
function oipCalculateCVWAP(candles) {
    return oipCalculateVWAP(candles);
}

// PVWAP — Previous-session VWAP. For every candle of a given day the value is the
// FINAL (closing) VWAP of the *previous* trading day, drawn as a flat line across
// the current session. Mirrors the "Previous VWAP" plot in the
// "Current & Previous VWAP Strategy" Pine script.
function oipCalculatePVWAP(candles) {
    if (!candles || candles.length === 0) return [];
    const dateOf = (t) => {
        const d = new Date(t * 1000);
        // UTC methods match the 'Fake IST Epoch' the server emits.
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    };

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
    const dateOf = (t) => {
        const d = new Date(t * 1000);
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    };

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

function oipCalculateDynamicCPR(candles) {
    if (!candles || !candles.length) return null;
    const days = []; let currentDay = null;
    candles.forEach(c => {
        const d = new Date(c.time * 1000);
        // Use UTC methods to match the 'Fake IST Epoch' from server
        const ds = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
        if (!currentDay || currentDay.date !== ds) {
            if (currentDay) days.push(currentDay);
            currentDay = { date: ds, isoDate: ds, high: c.high, low: c.low, close: c.close, times: [], closes: [] };
        }
        currentDay.high = Math.max(currentDay.high, c.high);
        currentDay.low = Math.min(currentDay.low, c.low);
        currentDay.close = c.close;
        currentDay.times.push(c.time);
        currentDay.closes.push(c.close);
    });
    if (currentDay) days.push(currentDay);
    let daysData = [];
    for (let i = 1; i < days.length; i++) {
        const prev = days[i - 1], curr = days[i];
        let oH = prev.high, oL = prev.low, oC = prev.close;
        if (oipOIData?.daily_ohlc?.[prev.isoDate]) { const t = oipOIData.daily_ohlc[prev.isoDate]; oH = t.high; oL = t.low; oC = t.close; }
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

function oipDrawCpr(candles) {
    if (!oipOIChart || !oipOISeries) return;

    const show = oipElems.showCpr?.checked;
    Object.values(oipCprSeriesMap).forEach(s => s.setData([]));
    if (!show || !candles || !candles.length) return;

    const daysData = oipCalculateDynamicCPR(candles);
    if (!daysData) return;

    const lineStyles = {
        prevH: { color: '#ef07f9', lineWidth: 1 },
        prevL: { color: '#ef07f9', lineWidth: 1 },
        pp:    { color: '#00008B', lineWidth: 1 },
        bc:    { color: '#00008B', lineWidth: 1 },
        tc:    { color: '#00008B', lineWidth: 1 },
        r1:    { color: '#006400', lineWidth: 1 },
        r2:    { color: '#006400', lineWidth: 1 },
        r3:    { color: '#006400', lineWidth: 1 },
        r4:    { color: '#006400', lineWidth: 1 },
        s1:    { color: '#ff0000', lineWidth: 1 },
        s2:    { color: '#ff0000', lineWidth: 1 },
        s3:    { color: '#ff0000', lineWidth: 1 },
        s4:    { color: '#ff0000', lineWidth: 1 },
        cr3:   { color: '#a020f0', lineWidth: 2 },
        cs3:   { color: '#a020f0', lineWidth: 2 }
    };

    const boxColors = {
        'cpr':   'rgba(51, 102, 255, 0.2)',   // #3366ff @ 20%
        'r1_r2': 'rgba(0, 204, 102, 0.02)',
        'r2_r3': 'rgba(0, 204, 102, 0.02)',
        'r3_r4': 'rgba(0, 204, 102, 0.02)',
        's1_s2': 'rgba(255, 0, 0, 0.02)',
        's2_s3': 'rgba(255, 0, 0, 0.02)',
        's3_s4': 'rgba(255, 0, 0, 0.02)'
    };

    const keyGroup = {
        prevH: 'oipCprShowPrevHL', prevL: 'oipCprShowPrevHL',
        pp: 'oipCprShowBand',      bc: 'oipCprShowBand',    tc: 'oipCprShowBand',
        r1: 'oipCprShowResistance', r2: 'oipCprShowResistance', r3: 'oipCprShowResistance', r4: 'oipCprShowResistance',
        s1: 'oipCprShowSupport',   s2: 'oipCprShowSupport', s3: 'oipCprShowSupport', s4: 'oipCprShowSupport',
        cr3: 'oipCprShowCumR3S3',  cs3: 'oipCprShowCumR3S3'
    };
    // Same grouping, mapped to line-style keys (one dropdown per checkbox group).
    const styleKeyGroup = {
        prevH: 'cprPrevHL', prevL: 'cprPrevHL',
        pp: 'cprBand', bc: 'cprBand', tc: 'cprBand',
        r1: 'cprResistance', r2: 'cprResistance', r3: 'cprResistance', r4: 'cprResistance',
        s1: 'cprSupport', s2: 'cprSupport', s3: 'cprSupport', s4: 'cprSupport',
        cr3: 'cprCumR3S3', cs3: 'cprCumR3S3'
    };
    const boxGroup = {
        'cpr':   'oipCprShowBand',
        'r1_r2': 'oipCprShowResistance', 'r2_r3': 'oipCprShowResistance', 'r3_r4': 'oipCprShowResistance',
        's1_s2': 'oipCprShowSupport',    's2_s3': 'oipCprShowSupport',    's3_s4': 'oipCprShowSupport'
    };
    const subChecked = id => document.getElementById(id)?.checked !== false;

    daysData.forEach((day, dayIdx) => {
        // Draw the box fills FIRST so the pivot lines (PP/BC/TC, R/S) render on
        // top of them — otherwise an opaque band fill hides the lines.
        day.boxes.forEach((box, boxIdx) => {
            const seriesKey = `box_${box.type}_${dayIdx}_${boxIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                const col = boxColors[box.type];
                series = oipOIChart.addSeries(LightweightCharts.BaselineSeries, {
                    baseValue: { type: 'price', price: box.min },
                    topFillColor1: col, topFillColor2: col, topLineColor: 'transparent',
                    bottomFillColor1: col, bottomFillColor2: col, bottomLineColor: 'transparent',
                    lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            if (!subChecked(boxGroup[box.type])) { series.setData([]); return; }
            series.applyOptions({ baseValue: { type: 'price', price: box.min } });
            series.setData(day.times.map(t => ({ time: t, value: box.max })));
        });

        Object.keys(day.levels).forEach(key => {
            const seriesKey = `line_${key}_${dayIdx}`;
            let series = oipCprSeriesMap[seriesKey];
            if (!series) {
                series = oipOIChart.addSeries(LightweightCharts.LineSeries, {
                    ...lineStyles[key],
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                oipCprSeriesMap[seriesKey] = series;
            }
            // Series are cached and reused across redraws — always re-apply the
            // current color/width/style choice, not just at first creation.
            const sk = styleKeyGroup[key];
            series.applyOptions({ lineStyle: oipGetLineStyle(sk), color: oipGetLineColor(sk), lineWidth: oipGetLineWidth(sk) });
            const val = day.levels[key];
            const visible = subChecked(keyGroup[key]);
            series.setData(visible && val != null && !isNaN(val) ? day.times.map(t => ({ time: t, value: val })) : []);
        });
    });
    oipApplyZOrder();
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
