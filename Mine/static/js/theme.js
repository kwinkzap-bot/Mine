/**
 * theme.js — shared theme-switching logic for the app's 5 themes
 * (light, dark, forest, cream, ocean).
 *
 * Consolidates what used to be copy-pasted per page: reading the active
 * theme from localStorage (the same 3-key fallback chain), toggling
 * theme classes on body (and, on pages with their own scoped container
 * like .oip-page/.mkt-page/.td-page, on that container too), persisting
 * the choice, and notifying every other open component via the
 * 'themechanged' window event.
 *
 * Load this early in <head>, before any page script that reads
 * window.AppTheme. It only defines functions — nothing here touches the
 * DOM at load time, so it's safe to load before <body> exists.
 */
(function (global) {
    'use strict';

    const THEMES = ['dark', 'forest', 'light', 'cream', 'ocean'];
    const THEME_CLASSES = THEMES.map(t => t + '-theme');
    const THEME_ICONS = {
        dark: '🌌',
        forest: '🌲',
        light: '☀️',
        cream: '📜',
        ocean: '🌊'
    };
    // Legacy per-page keys some pages still read directly; kept in sync so
    // any not-yet-migrated code that checks one of these still works.
    const STORAGE_KEYS = ['app-theme', 'oip-theme', 'mkt-theme'];
    const DEFAULT_THEME = 'ocean';

    function getActiveTheme() {
        for (const key of STORAGE_KEYS) {
            const v = localStorage.getItem(key);
            if (v) return v;
        }
        return DEFAULT_THEME;
    }

    function persistTheme(theme) {
        STORAGE_KEYS.forEach(key => localStorage.setItem(key, theme));
    }

    // Removes every known theme class from `el` and adds `${theme}-theme`.
    // Safe to call with a theme an element has no matching CSS for (e.g.
    // passing 'dark' to a page container that only styles light/forest/
    // cream/ocean) — the class is simply inert there.
    function applyThemeClass(el, theme) {
        if (!el) return;
        el.classList.remove(...THEME_CLASSES);
        if (THEMES.includes(theme)) el.classList.add(theme + '-theme');
    }

    // Applies the theme to <body> plus any extra page-scoped containers and
    // persists it, WITHOUT dispatching 'themechanged' — for a page syncing
    // itself to the already-active theme on load (nothing else needs to
    // hear about a theme that isn't changing).
    function syncTheme(theme, extraEls = []) {
        applyThemeClass(document.body, theme);
        extraEls.forEach(el => applyThemeClass(el, theme));
        persistTheme(theme);
    }

    // Same, but also dispatches 'themechanged' — use this for an actual
    // user-initiated theme change (e.g. a toggle button click) so other
    // open components/pages can react.
    function setTheme(theme, { persist = true, extraEls = [] } = {}) {
        applyThemeClass(document.body, theme);
        extraEls.forEach(el => applyThemeClass(el, theme));
        if (persist) persistTheme(theme);
        global.dispatchEvent(new CustomEvent('themechanged', { detail: { theme } }));
    }

    function cycleTheme(current) {
        const idx = THEMES.indexOf(current);
        return THEMES[(idx + 1) % THEMES.length];
    }

    global.AppTheme = {
        THEMES,
        THEME_ICONS,
        getActiveTheme,
        persistTheme,
        applyThemeClass,
        syncTheme,
        setTheme,
        cycleTheme
    };
})(window);
