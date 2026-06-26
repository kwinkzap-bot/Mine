/**
 * panel-system.js — Reusable, dynamic, resizable & draggable floating panels.
 *
 * Drop-in usable on ANY page:
 *   1. Include  css/panel-system.css  and this file.
 *   2. Register reusable content types (optional):
 *        PanelSystem.register('order-pad', (panel) => buildOrderPad(panel));
 *   3. Create panels imperatively from any route/component:
 *        PanelSystem.create({ title: 'Watchlist', content: '<div>…</div>' });
 *        PanelSystem.add('order-pad', { x: 200, y: 120 });
 *
 * Every panel is:
 *   - Draggable  (grab the header)
 *   - Resizable  (8 edge/corner handles — width AND height)
 *   - Min / Max / Close  buttons
 *   - Geometry persisted to localStorage (per panel id)
 *
 * No external dependencies.
 */
(function (window, document) {
    'use strict';

    const STORE_PREFIX = 'panelsys:';
    let zCounter = 1000;
    let uid = 0;

    /* ───────────────────────── helpers ───────────────────────── */
    const clamp = (v, min, max) => Math.min(Math.max(v, min), max);

    function loadGeometry(key) {
        try {
            const raw = localStorage.getItem(STORE_PREFIX + key);
            return raw ? JSON.parse(raw) : null;
        } catch (_) { return null; }
    }
    function saveGeometry(key, geo) {
        try { localStorage.setItem(STORE_PREFIX + key, JSON.stringify(geo)); } catch (_) {}
    }
    function clearGeometry(key) {
        try { localStorage.removeItem(STORE_PREFIX + key); } catch (_) {}
    }

    /* ───────────────────────── Panel class ───────────────────── */
    class Panel {
        constructor(opts) {
            this.opts = Object.assign({
                title: 'Panel',
                icon: '▪',
                content: '',
                width: 320,
                height: 260,
                x: null,            // null → auto-cascade
                y: null,
                minWidth: 220,
                minHeight: 120,
                resizable: true,
                draggable: true,
                closable: true,
                persist: true,       // remember geometry
                container: null,     // defaults to document.body
                onClose: null,
                onResize: null,
                type: null,          // registered type, if any
            }, opts || {});

            this.id = this.opts.id || ('panel-' + (++uid));
            this.persistKey = this.opts.persist ? (this.opts.persistKey || this.id) : null;
            this.minimized = false;
            this.maximized = false;
            this._build();
            PanelSystem._panels.set(this.id, this);
        }

        _build() {
            const o = this.opts;
            const host = o.container || document.body;

            const el = document.createElement('div');
            el.className = 'tp-panel';
            el.id = this.id;
            el.setAttribute('role', 'dialog');
            el.setAttribute('aria-label', o.title);

            // header
            const header = document.createElement('div');
            header.className = 'tp-panel__header';
            header.innerHTML = `
                <span class="tp-panel__title"><span class="tp-panel__icon">${o.icon}</span><span class="tp-panel__title-text">${o.title}</span></span>
                <span class="tp-panel__actions">
                    <button class="tp-panel__btn" data-act="min" title="Minimize">—</button>
                    <button class="tp-panel__btn" data-act="max" title="Maximize">▢</button>
                    ${o.closable ? '<button class="tp-panel__btn tp-panel__btn--close" data-act="close" title="Close">✕</button>' : ''}
                </span>`;

            // body
            const body = document.createElement('div');
            body.className = 'tp-panel__body';
            if (typeof o.content === 'string') body.innerHTML = o.content;
            else if (o.content instanceof Node) body.appendChild(o.content);

            el.appendChild(header);
            el.appendChild(body);

            // resize handles (8 directions → width + height)
            if (o.resizable) {
                ['n','s','e','w','ne','nw','se','sw'].forEach(dir => {
                    const h = document.createElement('div');
                    h.className = 'tp-resize tp-resize--' + dir;
                    h.dataset.dir = dir;
                    el.appendChild(h);
                });
            }

            host.appendChild(el);
            this.el = el;
            this.header = header;
            this.body = body;

            // initial geometry (persisted > opts > cascade)
            const saved = this.persistKey ? loadGeometry(this.persistKey) : null;
            const cascade = (PanelSystem._panels.size % 8) * 26;
            const geo = saved || {
                x: o.x != null ? o.x : 40 + cascade,
                y: o.y != null ? o.y : 40 + cascade,
                width: o.width,
                height: o.height,
            };
            this.setGeometry(geo);

            this._wireControls();
            if (o.draggable) this._wireDrag();
            if (o.resizable) this._wireResize();
            this.focus();
        }

        setGeometry({ x, y, width, height }) {
            const vw = window.innerWidth, vh = window.innerHeight;
            const w = clamp(width,  this.opts.minWidth,  vw);
            const h = clamp(height, this.opts.minHeight, vh);
            const nx = clamp(x, 0, Math.max(0, vw - 60));
            const ny = clamp(y, 0, Math.max(0, vh - 40));
            Object.assign(this.el.style, {
                left: nx + 'px', top: ny + 'px',
                width: w + 'px', height: h + 'px',
            });
        }

        getGeometry() {
            const s = this.el.style;
            return {
                x: parseInt(s.left, 10) || 0,
                y: parseInt(s.top, 10) || 0,
                width: parseInt(s.width, 10) || this.opts.width,
                height: parseInt(s.height, 10) || this.opts.height,
            };
        }

        _persist() {
            if (this.persistKey && !this.maximized) saveGeometry(this.persistKey, this.getGeometry());
        }

        focus() {
            this.el.style.zIndex = ++zCounter;
            document.querySelectorAll('.tp-panel.is-active').forEach(p => p.classList.remove('is-active'));
            this.el.classList.add('is-active');
        }

        _wireControls() {
            this.el.addEventListener('mousedown', () => this.focus(), true);
            this.header.querySelectorAll('[data-act]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const act = btn.dataset.act;
                    if (act === 'min')   this.toggleMinimize();
                    if (act === 'max')   this.toggleMaximize();
                    if (act === 'close') this.close();
                });
            });
        }

        toggleMinimize() {
            this.minimized = !this.minimized;
            this.el.classList.toggle('is-minimized', this.minimized);
            if (this.minimized) {
                this._restoreH = this.el.style.height;
                this.el.style.height = '';
            } else if (this._restoreH) {
                this.el.style.height = this._restoreH;
            }
        }

        toggleMaximize() {
            this.maximized = !this.maximized;
            if (this.maximized) {
                this._restoreGeo = this.getGeometry();
                this.el.classList.add('is-maximized');
            } else {
                this.el.classList.remove('is-maximized');
                if (this._restoreGeo) this.setGeometry(this._restoreGeo);
            }
        }

        close() {
            if (typeof this.opts.onClose === 'function' && this.opts.onClose(this) === false) return;
            this.el.remove();
            PanelSystem._panels.delete(this.id);
            if (this.persistKey) clearGeometry(this.persistKey);
        }

        /* drag the panel by its header */
        _wireDrag() {
            let sx, sy, ox, oy, dragging = false;
            const onMove = (e) => {
                if (!dragging) return;
                const g = this.getGeometry();
                this.setGeometry({ ...g, x: ox + (e.clientX - sx), y: oy + (e.clientY - sy) });
            };
            const onUp = () => {
                if (!dragging) return;
                dragging = false;
                document.body.classList.remove('tp-no-select');
                window.removeEventListener('mousemove', onMove);
                window.removeEventListener('mouseup', onUp);
                this._persist();
            };
            this.header.addEventListener('mousedown', (e) => {
                if (e.target.closest('.tp-panel__btn') || this.maximized) return;
                dragging = true;
                sx = e.clientX; sy = e.clientY;
                const g = this.getGeometry(); ox = g.x; oy = g.y;
                document.body.classList.add('tp-no-select');
                window.addEventListener('mousemove', onMove);
                window.addEventListener('mouseup', onUp);
            });
        }

        /* resize via any of the 8 handles — controls width AND height */
        _wireResize() {
            let dir, sx, sy, start;
            const onMove = (e) => {
                const dx = e.clientX - sx, dy = e.clientY - sy;
                let { x, y, width, height } = start;
                if (dir.includes('e')) width  = start.width  + dx;
                if (dir.includes('s')) height = start.height + dy;
                if (dir.includes('w')) { width  = start.width  - dx; x = start.x + dx; }
                if (dir.includes('n')) { height = start.height - dy; y = start.y + dy; }
                // respect minimums while anchoring opposite edge
                if (width  < this.opts.minWidth  && dir.includes('w')) x = start.x + (start.width  - this.opts.minWidth);
                if (height < this.opts.minHeight && dir.includes('n')) y = start.y + (start.height - this.opts.minHeight);
                this.setGeometry({ x, y, width, height });
                if (typeof this.opts.onResize === 'function') this.opts.onResize(this.getGeometry(), this);
            };
            const onUp = () => {
                document.body.classList.remove('tp-no-select');
                window.removeEventListener('mousemove', onMove);
                window.removeEventListener('mouseup', onUp);
                this._persist();
            };
            this.el.querySelectorAll('.tp-resize').forEach(handle => {
                handle.addEventListener('mousedown', (e) => {
                    if (this.maximized || this.minimized) return;
                    e.preventDefault(); e.stopPropagation();
                    dir = handle.dataset.dir; sx = e.clientX; sy = e.clientY;
                    start = this.getGeometry();
                    this.focus();
                    document.body.classList.add('tp-no-select');
                    window.addEventListener('mousemove', onMove);
                    window.addEventListener('mouseup', onUp);
                });
            });
        }

        setTitle(t) { this.header.querySelector('.tp-panel__title-text').textContent = t; }
        setContent(html) {
            if (typeof html === 'string') this.body.innerHTML = html;
            else { this.body.innerHTML = ''; this.body.appendChild(html); }
        }
    }

    /* ───────────────────────── public API ───────────────────── */
    const PanelSystem = {
        _panels: new Map(),
        _types: new Map(),

        /** Register a reusable content type. factory(panel) → fills panel.body. */
        register(type, factory) { this._types.set(type, factory); return this; },

        /** List registered type names (for "Add panel" menus). */
        types() { return Array.from(this._types.keys()); },

        /** Create a free-form panel. */
        create(opts) { return new Panel(opts); },

        /** Create a panel from a registered type. */
        add(type, opts) {
            const factory = this._types.get(type);
            if (!factory) { console.warn('[PanelSystem] unknown type:', type); return null; }
            const meta = (factory.meta) || {};
            const panel = new Panel(Object.assign({ type, title: meta.title || type, icon: meta.icon },
                                                  meta.defaults || {}, opts || {}));
            factory(panel);
            return panel;
        },

        get(id) { return this._panels.get(id); },
        all() { return Array.from(this._panels.values()); },
        closeAll() { this.all().forEach(p => p.close()); },

        /** Forget every persisted geometry (reset layout). */
        resetLayout() {
            Object.keys(localStorage).filter(k => k.startsWith(STORE_PREFIX))
                .forEach(k => localStorage.removeItem(k));
        },
    };

    // keep panels on-screen after a viewport resize
    window.addEventListener('resize', () => {
        PanelSystem.all().forEach(p => { if (!p.maximized) p.setGeometry(p.getGeometry()); });
    });

    window.PanelSystem = PanelSystem;
})(window, document);
