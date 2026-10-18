/* Keep destination filters and return links in step with HTMX's current URL. */
(() => {
    const viewPaths = new Set(['/desk/cases/', '/desk/cases/kanban/', '/desk/cases/calendar/']);
    const storageKey = 'jk_task_return_' + (document.body.dataset.userId || '');
    const currentView = () => viewPaths.has(location.pathname);
    const remember = () => {
        if (currentView()) {
            try { sessionStorage.setItem(storageKey, location.pathname + location.search); } catch (_) {}
        }
    };
    const normalize = query => {
        const params = new URLSearchParams(query);
        for (const [key, value] of Array.from(params)) if (!value || key === 'page' || key === 'date_source') params.delete(key);
        params.sort();
        return params.toString();
    };
    const updateSavedFilters = () => {
        document.querySelectorAll('[data-saved-query]').forEach(link => {
            const active = normalize(link.dataset.savedQuery) === normalize(location.search);
            link.toggleAttribute('aria-current', active);
            link.closest('span').classList.toggle('jk-saved-filter-active', active);
        });
    };
    document.addEventListener('click', event => {
        const link = event.target.closest('a[href]');
        if (!link) return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin) return;
        if (link.closest('.jk-sidebar-nav') && link.classList.contains('active') &&
            (url.pathname === location.pathname || (currentView() && url.pathname === '/desk/cases/'))) {
            // Repeated menu clicks reload the current view with its existing filters.
            link.href = location.href;
        } else if (link.hasAttribute('data-task-view')) {
            url.search = location.search;
            url.searchParams.delete('page');
            link.href = url.href;
        } else if (currentView() && /^\/desk\/cases\/[0-9a-f-]+\/$/i.test(url.pathname)) {
            url.searchParams.set('back', location.pathname + location.search);
            link.href = url.href;
        }
        remember();
    }, true);
    document.addEventListener('DOMContentLoaded', () => {
        remember();
        updateSavedFilters();
        if (!currentView() && /^\/desk\/cases\/[0-9a-f-]+\/$/i.test(location.pathname)) {
            let back = new URLSearchParams(location.search).get('back');
            if (!back) { try { back = sessionStorage.getItem(storageKey); } catch (_) {} }
            if (back) {
                const url = new URL(back, location.origin);
                if (url.origin === location.origin && viewPaths.has(url.pathname)) {
                    document.querySelectorAll('input[name="back_url"]').forEach(input => { input.value = url.pathname + url.search; });
                    const link = document.querySelector('a[title="Back to Tickets"]');
                    if (link) link.href = url.href;
                }
            }
        }
        const topbar = document.querySelector('.jk-topbar');
        const toolbar = document.querySelector('.jk-my-tickets-toolbar');
        const measure = () => {
            if (topbar) document.documentElement.style.setProperty('--jk-topbar-height', `${topbar.getBoundingClientRect().height}px`);
            if (toolbar) toolbar.parentElement.style.setProperty('--jk-ticket-toolbar-height', `${toolbar.getBoundingClientRect().height}px`);
        };
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(measure);
            if (topbar) observer.observe(topbar);
            if (toolbar) observer.observe(toolbar);
        }
        measure();
    });
    document.body.addEventListener('htmx:pushedIntoHistory', () => { remember(); updateSavedFilters(); });
})();
