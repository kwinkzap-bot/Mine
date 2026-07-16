/* ================================================================
   DataGrid — builds the app's standard grid markup from a column config.

   The design lives in static/css/components/data-grid.css; this file is
   the only thing that should be writing .dg-* markup. Pages describe
   *what* the columns mean and the component decides how they look, so a
   change to the grid design is one edit rather than eleven.

   Usage — the Algo "Executed Trades" grid, which is the reference:

       DataGrid.card({
           title: 'Executed Trades — Today',
           meta:  trades.length + ' trades',
           empty: 'No trades executed today',
           rows:  trades,
           columns: [
               { key: 'label',     label: 'Logic Type', strong: true },
               { key: 'mode',      label: 'Mode',       badge: m => m === 'paper' ? 'warn' : 'pos' },
               { key: 'direction', label: 'Direction',  strong: true,
                 tone: d => d === 'BUY' ? 'pos' : 'neg' },
               { key: 'opt_pnl_inr', label: 'Opt P&L (₹)', align: 'right', strong: true,
                 format: DataGrid.inr, tone: DataGrid.sign },
           ],
       });

   Column options
     key      Property read from the row. Omit when using `render`.
     label    Header text.
     align    'left' (default) | 'right' | 'center'.
     strong   Bold the cell.
     format   (value, row) => string. Return plain text; it is escaped.
              Defaults to the raw value, with null/undefined shown as '—'.
     tone     'pos' | 'neg' | 'warn' | 'muted', or (value, row) => tone.
              Colours the cell.
     badge    Renders the cell as a badge. Either a tone string or
              (value, row) => tone. Label comes from `format`/value.
     render   (value, row) => HTML string. Full escape hatch for cells the
              options above can't express — caller must escape its own
              interpolations. Skips format/tone/badge.
     cellClass Extra class(es) on the <td>, or (value, row) => string.
     thClass  Extra class(es) on this column's <th> — for a page accenting
              one header (a "LAST price" column, say). Static string only:
              headers render once per grid, not per row.

   Grid options
     columns  Column config array (required).
     rows     Array of row objects (required).
     empty    Message shown when `rows` is empty.
     rowClass (row, index) => string. Extra class(es) on the <tr>.
     rowAttrs (row, index) => string of HTML attributes, e.g. filter hooks
              like `data-status="active"`. Caller must escape values.
     foot     Array of {label, colspan?, align?, tone?, cellClass?} — a single
              totals row rendered in <tfoot>. `label` is raw HTML (not
              escaped): callers must escape their own interpolations.

   ── Click-to-sort ──────────────────────────────────────────────────
   Every page that had a sortable table (CPR scanner, EMA touch/narrow,
   Portfolio, Backtest) carried its own copy of the same click-header/
   reorder-rows logic — this is that logic, once, shared.

   Add to a column: `sortable: true`. The column's own `key` is the sort
   field by default; use `sortKey` to sort by a different field than what's
   displayed, or `sortValue: row => comparable` for a value that isn't a
   plain field (e.g. a computed P&L). A column with `sortable` but no key,
   sortKey, or sortValue is left unclickable rather than throwing.

   Use `DataGrid.mountSortable(container, opts)` instead of `render`/`mount`
   — same opts shape, but it owns click handling and re-render, and persists
   sort state on the container across repeated calls (e.g. a periodic data
   refresh keeps the user's current sort). Sorting compares the underlying
   field value, not the formatted text — numbers sort as numbers even if
   `format` prints "₹1,234" or "12.5%", so there's no currency-stripping
   regex to get wrong.

   Two more mountSortable-only opts:
     defaultSort  {key, dir} — pre-select a column's sort on first render
                  (a results grid that should open best-first, say), instead
                  of defaulting to arrival order.
     onSorted     (rows) => void — called with the exact array in its current
                  display order on every render. Lets a "Use this row" button
                  rendered with a plain row index look the row back up later,
                  instead of re-deriving the same sort outside the component.

   Every format/render/tone/badge/cellClass callback also receives the row's
   index as a 3rd argument — (value, row, index) — for a "#" column or a
   per-row control that needs to key off its own position.

       DataGrid.mountSortable('highIvBody', {
           rows: stocks,
           columns: [
               { key: 'symbol', label: 'Symbol', sortable: true, strong: true },
               { key: 'iv_percentile', label: 'IV Pctl %', sortable: true,
                 align: 'right', format: v => v.toFixed(1) + '%' },
           ],
       });
   ================================================================ */

(function (global) {
    'use strict';

    const EM_DASH = '—';

    const escape = (v) => String(v ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const isBlank = (v) => v == null || v === '';

    // Resolve an option that may be a literal or a (value, row, index) callback.
    const resolve = (opt, value, row, i) =>
        typeof opt === 'function' ? opt(value, row, i) : opt;

    // ── Formatters — the shapes this app repeats everywhere ──────────
    // Every formatter is strictly single-argument, because `format` is invoked
    // as format(value, row): a second `dp` parameter would silently collect the
    // row object. Use `.dp(n)` when you need different precision —
    //     { key: 'qty', format: DataGrid.number.dp(0) }
    // — rather than passing precision positionally.
    const withDp = (fn, defaultDp) => {
        const bound = (v) => fn(v, defaultDp);
        bound.dp = (n) => (v) => fn(v, n);
        return bound;
    };

    // 1234.5 → "₹1,234.50"
    const _rupees = (v, dp) => isBlank(v) ? EM_DASH :
        '₹' + Number(v).toLocaleString('en-IN', {
            minimumFractionDigits: dp, maximumFractionDigits: dp,
        });

    // -3.9 → "-3.90"   |   2 → "+2.00"
    const _points = (v, dp) => isBlank(v) ? EM_DASH :
        (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(dp);

    const _number = (v, dp) => isBlank(v) ? EM_DASH : Number(v).toFixed(dp);

    const rupees = withDp(_rupees, 2);
    const points = withDp(_points, 2);
    const number = withDp(_number, 2);

    // -1333 → "-₹1,333"   |   130 → "+₹130". Whole rupees by definition — a
    // paise-level P&L is noise at a glance — so there is no precision knob.
    const inr = (v) => isBlank(v) ? EM_DASH :
        (Number(v) >= 0 ? '+₹' : '-₹') +
        Math.abs(Math.round(Number(v))).toLocaleString('en-IN');

    // ── Tones ────────────────────────────────────────────────────────
    // Pass by reference as `tone:` on any numeric column that is a P&L.
    const sign = (v) => isBlank(v) ? '' : (Number(v) >= 0 ? 'pos' : 'neg');

    const badgeHtml = (label, tone) =>
        `<span class="dg-badge dg-badge--${escape(tone || 'neutral')}">${escape(label)}</span>`;

    // ── Cell ─────────────────────────────────────────────────────────
    // `i` is the row's position in the (already sorted, if applicable) rows
    // array being rendered — a row-number column ("#") or a per-row action
    // control that needs to reference its own row (a "Use" button) reads it
    // as the 3rd argument to format/render/tone/badge/cellClass.
    function cell(col, row, i) {
        const value = col.key ? row[col.key] : undefined;

        const cls = ['dg-td'];
        if (col.align === 'right')  cls.push('dg-td--right');
        if (col.align === 'center') cls.push('dg-td--center');
        if (col.strong)             cls.push('dg-td--strong');

        const extra = resolve(col.cellClass, value, row, i);
        if (extra) cls.push(extra);

        // `render` bypasses the formatting pipeline entirely.
        if (col.render) {
            return `<td class="${cls.join(' ')}">${col.render(value, row, i)}</td>`;
        }

        const text = col.format
            ? col.format(value, row, i)
            : (isBlank(value) ? EM_DASH : String(value));

        if (col.badge != null) {
            // A blank value is not a status — show the dash, not an empty badge.
            const inner = isBlank(value)
                ? EM_DASH
                : badgeHtml(text, resolve(col.badge, value, row, i));
            return `<td class="${cls.join(' ')}">${inner}</td>`;
        }

        const tone = resolve(col.tone, value, row, i);
        if (tone) cls.push('dg-' + (tone === 'muted' ? 'muted' : tone === 'warn' ? 'warn' : tone));

        return `<td class="${cls.join(' ')}">${escape(text)}</td>`;
    }

    // ── Footer ───────────────────────────────────────────────────────
    // opts.foot is an array of cells for a single <tfoot> row — a totals line.
    // Each cell is { label, colspan?, align?, tone?, cellClass? }. `label` is
    // taken as already-safe HTML (totals are computed, not user text), so
    // callers must escape anything they interpolate from data.
    function footRow(foot) {
        const cells = foot.map(c => {
            const cls = ['dg-foot-td'];
            if (c.align === 'right')  cls.push('dg-td--right');
            if (c.align === 'center') cls.push('dg-td--center');
            const tone = resolve(c.tone, c.label);
            if (tone) cls.push('dg-' + tone);
            if (c.cellClass) cls.push(c.cellClass);
            const span = c.colspan ? ` colspan="${c.colspan}"` : '';
            return `<td class="${cls.join(' ')}"${span}>${c.label ?? ''}</td>`;
        }).join('');
        return `<tfoot><tr class="dg-foot-tr">${cells}</tr></tfoot>`;
    }

    // A column is sortable only if it also says what to sort by — a bare
    // `sortable: true` on a computed column with no key/sortValue is inert
    // rather than a runtime error when someone eventually clicks it.
    //
    // Every sortable column gets a stable id: sortKey/key when there is one,
    // else 'col<index>' for a purely computed column (sortValue only). The
    // index fallback keeps two computed sortable columns from colliding on
    // the same id — without it, clicking one would render both as "active".
    const sortIdOf   = (col, idx) => col.sortKey || col.key || ('col' + idx);
    const isSortable = (col) => !!(col.sortable && (col.sortKey || col.key || col.sortValue));

    // ── Grid ─────────────────────────────────────────────────────────
    function render(opts) {
        const { columns, rows = [], empty = 'No data' } = opts;
        const sortState = opts.sortState || {};

        if (!rows.length) return `<div class="dg-empty">${escape(empty)}</div>`;

        const head = columns.map((c, idx) => {
            const cls = ['dg-th'];
            if (c.align === 'right')  cls.push('dg-th--right');
            if (c.align === 'center') cls.push('dg-th--center');
            if (c.thClass)            cls.push(c.thClass);

            let attrs = '';
            if (isSortable(c)) {
                const id = sortIdOf(c, idx);
                cls.push('dg-th--sortable');
                attrs = ` data-sort-key="${escape(id)}" role="button" tabindex="0"`;
                if (sortState.key === id) {
                    cls.push('dg-th--sort-' + sortState.dir);
                    attrs += ` aria-sort="${sortState.dir === 'asc' ? 'ascending' : 'descending'}"`;
                }
            }
            return `<th class="${cls.join(' ')}"${attrs}>${escape(c.label ?? '')}</th>`;
        }).join('');

        const body = rows.map((row, i) => {
            const cls   = opts.rowClass ? opts.rowClass(row, i) : '';
            const attrs = opts.rowAttrs ? opts.rowAttrs(row, i) : '';
            return `<tr${cls ? ` class="${cls}"` : ''}${attrs ? ' ' + attrs : ''}>` +
                   columns.map(c => cell(c, row, i)).join('') +
                   `</tr>`;
        }).join('');

        const foot = opts.foot && opts.foot.length ? footRow(opts.foot) : '';

        return `<div class="dg-scroll">` +
               `<table class="dg-table">` +
               `<thead><tr>${head}</tr></thead>` +
               `<tbody>${body}</tbody>` +
               foot +
               `</table></div>`;
    }

    // Grid wrapped in the standard titled card surface.
    function card(opts) {
        const meta = opts.meta
            ? `<span class="dg-card-meta">${escape(opts.meta)}</span>` : '';
        return `<div class="dg-card">` +
               `<div class="dg-card-hdr">` +
               `<span class="dg-card-title">${escape(opts.title ?? '')}</span>${meta}` +
               `</div>` +
               render(opts) +
               `</div>`;
    }

    // Render straight into a container element.
    function mount(target, opts) {
        const el = typeof target === 'string'
            ? document.getElementById(target) : target;
        if (el) el.innerHTML = render(opts);
        return el;
    }

    // ── Sorting ──────────────────────────────────────────────────────
    // Numeric-aware, like a spreadsheet: two numeric values compare as
    // numbers even if one is the string "12.5". Compares raw field values,
    // not rendered text, so a formatted "₹1,234" cell sorts correctly without
    // stripping currency symbols back out of it.
    function compareValues(a, b) {
        const an = typeof a === 'number' ? a : parseFloat(a);
        const bn = typeof b === 'number' ? b : parseFloat(b);
        const bothNumeric = a != null && a !== '' && b != null && b !== ''
            && !isNaN(an) && !isNaN(bn);
        if (bothNumeric) return an === bn ? 0 : (an < bn ? -1 : 1);
        return String(a ?? '').localeCompare(String(b ?? ''));
    }

    function sortRows(rows, col, dir) {
        const get = col.sortValue || (col.key ? (r => r[col.key]) : null);
        if (!get) return rows;
        const mul = dir === 'desc' ? -1 : 1;
        // Copy — never mutate the caller's array, which may be a live
        // reference the page reuses on its next refresh.
        return [...rows].sort((a, b) => compareValues(get(a), get(b)) * mul);
    }

    const _sortState = new WeakMap(); // container element -> { key, dir }
    const _lastOpts   = new WeakMap(); // container element -> most recent opts

    // Renders a sortable grid into `target` and owns its click-to-sort state.
    // Safe to call repeatedly with fresh `rows` (e.g. on each data refresh) —
    // the container keeps whatever sort the user has selected.
    function mountSortable(target, opts) {
        const el = typeof target === 'string'
            ? document.getElementById(target) : target;
        if (!el) return el;

        _lastOpts.set(el, opts);
        if (!_sortState.has(el)) {
            // opts.defaultSort: {key, dir} pre-selects a column (e.g. a results
            // grid that should open sorted best-first) instead of arrival order.
            const d = opts.defaultSort;
            _sortState.set(el, d ? { key: d.key, dir: d.dir || 'asc' } : { key: null, dir: 'asc' });
        }
        const state = _sortState.get(el);

        const activeCol = state.key
            ? opts.columns.find((c, i) => sortIdOf(c, i) === state.key && isSortable(c))
            : null;
        const rows = activeCol ? sortRows(opts.rows, activeCol, state.dir) : opts.rows;

        // opts.onSorted(rows, sortState): hands back the exact array in its
        // current (possibly sorted) display order, e.g. so a "Use this row"
        // button rendered with a plain row index can look the row back up
        // later — simpler than re-deriving the same sort outside the
        // component. `sortState` is handed over too (fired before render(),
        // so a `rowClass` closure reading state the caller stashed here sees
        // it already updated) — e.g. to highlight the leading row only while
        // a particular column is what's actively sorting the grid.
        if (opts.onSorted) opts.onSorted(rows, { ...state });

        el.innerHTML = render({ ...opts, rows, sortState: state });

        // Bind once per container — innerHTML is replaced on every render, but
        // the container node itself persists, so a delegated listener survives.
        if (!el.dataset.dgSortWired) {
            el.dataset.dgSortWired = '1';
            const onActivate = (th) => {
                const id = th.dataset.sortKey;
                if (state.key === id) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
                else { state.key = id; state.dir = 'asc'; }
                mountSortable(el, _lastOpts.get(el));
            };
            el.addEventListener('click', (e) => {
                const th = e.target.closest('[data-sort-key]');
                if (th && el.contains(th)) onActivate(th);
            });
            el.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return;
                const th = e.target.closest('[data-sort-key]');
                if (th && el.contains(th)) { e.preventDefault(); onActivate(th); }
            });
        }
        return el;
    }

    // Re-paints a container already mounted with mountSortable, using the
    // rows/columns from its last call — same rows and sort, but any render/
    // format/tone/badge callback that reads outside state (a "Live" badge
    // keyed off data that arrived after the last render, say) picks up the
    // change. A no-op if the container was never mounted.
    function refresh(target) {
        const el = typeof target === 'string'
            ? document.getElementById(target) : target;
        const opts = el && _lastOpts.get(el);
        if (opts) mountSortable(el, opts);
        return el;
    }

    global.DataGrid = {
        render, card, mount, mountSortable, refresh,
        escape, badge: badgeHtml,
        // formatters
        rupees, inr, points, number,
        // tones
        sign,
    };
})(window);
