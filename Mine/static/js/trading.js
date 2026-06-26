/**
 * trading.js — Customizable terminal built on PanelSystem.
 *
 * Each panel hosts one of THIS application's own routes/components (embedded
 * via ?embed=1 so the nav chrome is hidden). Add any component into a
 * draggable, resizable panel from the "Add Panel" menu.
 */
(function () {
    'use strict';

    const toast = (m, t) => (window.showNotification ? showNotification(m, t) : console.log(`[${t}] ${m}`));

    /* The application's components/routes that can be dropped into a panel.
       Add a line here to expose any new route as an addable panel. */
    const COMPONENTS = [
        { key: 'dashboard',  title: 'Dashboard',       icon: '📊', url: '/dashboard',       w: 720, h: 460 },
        { key: 'markets',    title: 'Markets',         icon: '🌐', url: '/markets',         w: 760, h: 480 },
        { key: 'oi-profile', title: 'OI Profile',      icon: '📉', url: '/oi-profile',      w: 700, h: 480 },
        { key: 'open-int',   title: 'Open Interest',   icon: '📈', url: '/open-interest',   w: 640, h: 440 },
        { key: 'historic-oi',title: 'Historic OI',     icon: '🗂', url: '/historic-oi',     w: 640, h: 420 },
        { key: 'contracts',  title: 'Contracts',       icon: '📜', url: '/contracts',       w: 760, h: 420 },
        { key: 'portfolio',  title: 'Portfolio',       icon: '💼', url: '/portfolio',       w: 560, h: 400 },
        { key: 'orders',     title: 'Orders',          icon: '🧾', url: '/orders',          w: 560, h: 400 },
        { key: 'backtest',   title: 'Backtest',        icon: '🧪', url: '/backtest',        w: 720, h: 480 },
        { key: 'scanners',   title: 'Scanners',        icon: '🔍', url: '/cpr-filter',      w: 700, h: 460 },
        { key: 'trend',      title: 'Trend Detection', icon: '🧭', url: '/trend-detection', w: 700, h: 460 },
        { key: 'algo',       title: 'Algo',            icon: '⚡', url: '/algo',            w: 640, h: 440 },
    ];
    const byKey = Object.fromEntries(COMPONENTS.map(c => [c.key, c]));

    /* Build an embedded-component panel: an iframe pointing at a real route. */
    function buildComponent(panel, comp) {
        panel.body.classList.add('tt-embed-body');
        const url = comp.url + (comp.url.includes('?') ? '&' : '?') + 'embed=1';

        const bar = document.createElement('div');
        bar.className = 'tt-embed-bar';
        bar.innerHTML = `<span class="tt-embed-url">${comp.url}</span>
            <span class="tt-embed-acts">
                <button class="tt-embed-btn" data-act="reload" title="Reload">↻</button>
                <a class="tt-embed-btn" href="${comp.url}" target="_blank" title="Open full page">↗</a>
            </span>`;

        const frame = document.createElement('iframe');
        frame.className = 'tt-embed-frame';
        frame.src = url;
        frame.loading = 'lazy';
        frame.setAttribute('title', comp.title);

        panel.body.innerHTML = '';
        panel.body.appendChild(bar);
        panel.body.appendChild(frame);

        bar.querySelector('[data-act="reload"]').addEventListener('click', () => {
            frame.src = frame.src;
        });
    }

    /* Register every component as a reusable panel type. */
    COMPONENTS.forEach(comp => {
        const factory = (panel) => buildComponent(panel, comp);
        factory.meta = {
            title: comp.title, icon: comp.icon,
            defaults: { width: comp.w, height: comp.h, minWidth: 320, minHeight: 220 },
        };
        PanelSystem.register('cmp:' + comp.key, factory);
    });

    /* ───────────────────── toolbar / bootstrap ───────────────────── */
    function buildAddMenu() {
        const menu = document.getElementById('tt-add-menu');
        if (!menu) return;
        menu.innerHTML = COMPONENTS
            .map(c => `<button class="tt-add-item" data-key="${c.key}">${c.icon} ${c.title}</button>`).join('');
        menu.addEventListener('click', (e) => {
            const b = e.target.closest('.tt-add-item'); if (!b) return;
            PanelSystem.add('cmp:' + b.dataset.key, { persist: false });
            menu.classList.remove('show');
        });
    }

    function defaultLayout() {
        // stable ids → resized/moved geometry persists across reloads
        PanelSystem.add('cmp:oi-profile', { id: 'tt-oi-profile', x: 16, y: 70 });
    }

    document.addEventListener('DOMContentLoaded', () => {
        buildAddMenu();

        document.getElementById('tt-add-btn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            document.getElementById('tt-add-menu')?.classList.toggle('show');
        });
        document.addEventListener('click', () => document.getElementById('tt-add-menu')?.classList.remove('show'));

        document.getElementById('tt-reset-btn')?.addEventListener('click', () => {
            PanelSystem.closeAll();
            PanelSystem.resetLayout();
            defaultLayout();
            toast('Layout reset', 'info');
        });

        defaultLayout();
    });
})();
