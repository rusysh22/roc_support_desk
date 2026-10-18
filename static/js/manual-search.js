/*
 * User Manual search — predictive dropdown, "stabilo" (highlighter-pen) marks,
 * visited-result memory, and in-page highlighting of the searched words.
 *
 *   ManualSearch.initBox({ input, dropdown, results, wrap, searchUrl })
 *       Typeahead: suggestions (page titles / headings) + top results, keyboard
 *       navigation, Enter opens the full results page.
 *   ManualSearch.markIn(element, terms)
 *       Wrap every occurrence of the terms inside element in <mark class="stabilo">.
 *   ManualSearch.highlightPage(container, query)
 *       Used by the viewer when it is opened from a search result (?hl=...):
 *       highlights the words, scrolls to the first one and shows a ▲/▼ bar.
 *
 * Result links carry ?hl=<query>; clicked results are remembered (localStorage)
 * and keep a stabilo mark so users can see what they already opened.
 */
(function () {
    const VISITED_KEY = 'manual_search_visited_v1';

    const CSS = `
    mark.stabilo{color:inherit;padding:.05em .28em;margin:0 -.08em;border-radius:.35em .8em .45em .9em;
      background:linear-gradient(100deg,rgba(253,224,71,0) .8%,rgba(253,224,71,.9) 2.5%,rgba(250,204,21,.75) 97%,rgba(253,224,71,0) 99%);
      -webkit-box-decoration-break:clone;box-decoration-break:clone;}
    mark.stabilo.stabilo-active{background:linear-gradient(100deg,rgba(251,146,60,0) .8%,rgba(251,146,60,.95) 2.5%,rgba(249,115,22,.8) 97%,rgba(251,146,60,0) 99%);
      box-shadow:0 0 0 3px rgba(251,146,60,.25);}
    .ms-section{padding:8px 14px 4px;font-size:10.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;}
    .ms-item{display:flex;align-items:flex-start;gap:10px;padding:9px 14px;text-decoration:none;color:inherit;cursor:pointer;border-left:3px solid transparent;transition:background .12s,border-color .12s;}
    .ms-item:hover,.ms-item.ms-active{background:#eef2ff;border-left-color:#6366f1;}
    .ms-item.ms-clicked .ms-title{background:linear-gradient(100deg,rgba(251,146,60,0),rgba(251,146,60,.55) 3%,rgba(251,146,60,.4) 97%,rgba(251,146,60,0));border-radius:.3em .8em;}
    .ms-item.ms-visited .ms-title{background:linear-gradient(100deg,rgba(253,224,71,0),rgba(253,224,71,.45) 3%,rgba(253,224,71,.3) 97%,rgba(253,224,71,0));border-radius:.3em .8em;}
    .ms-icon{flex-shrink:0;width:26px;height:26px;border-radius:8px;display:flex;align-items:center;justify-content:center;background:#f1f5f9;font-size:13px;}
    .ms-sugg .ms-icon{background:transparent;color:#94a3b8;}
    .ms-body{flex:1;min-width:0;}
    .ms-title{font-size:13px;font-weight:600;color:#0f172a;line-height:1.35;padding:0 .15em;margin-left:-.15em;}
    .ms-crumb{font-size:10.5px;color:#64748b;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .ms-snippet{font-size:11.5px;color:#475569;margin-top:2px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
    .ms-visited-tag{flex-shrink:0;font-size:10px;font-weight:700;color:#a16207;background:#fef9c3;border-radius:999px;padding:1px 7px;margin-top:2px;}
    .ms-footer{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 14px;font-size:11.5px;color:#4f46e5;font-weight:600;background:#f8fafc;border-top:1px solid #f1f5f9;cursor:pointer;}
    .ms-footer kbd{font:inherit;font-size:10px;color:#64748b;background:#fff;border:1px solid #e2e8f0;border-radius:5px;padding:1px 5px;}
    .ms-empty{padding:18px 14px;text-align:center;font-size:12px;color:#94a3b8;}
    .ms-hlbar{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:60;display:flex;align-items:center;gap:6px;
      background:#0f172a;color:#fff;border-radius:999px;padding:6px 8px 6px 14px;font-size:12.5px;box-shadow:0 10px 30px rgba(15,23,42,.3);max-width:calc(100vw - 32px);}
    .ms-hlbar b{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .ms-hlbar button{border:none;background:rgba(255,255,255,.12);color:#fff;width:28px;height:28px;border-radius:999px;cursor:pointer;font-size:13px;}
    .ms-hlbar button:hover{background:rgba(255,255,255,.25);}
    .ms-hlbar .ms-count{color:#cbd5e1;font-variant-numeric:tabular-nums;white-space:nowrap;}`;

    function injectCss() {
        if (document.getElementById('manual-search-css')) return;
        const s = document.createElement('style');
        s.id = 'manual-search-css';
        s.textContent = CSS;
        document.head.appendChild(s);
    }

    function esc(str) {
        return String(str == null ? '' : str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function termsOf(query) {
        const q = (query || '').toLowerCase().trim().replace(/\s+/g, ' ');
        if (!q) return [];
        const words = q.split(' ').filter(w => w.length > 1);
        // whole phrase first so it wins over its parts
        return Array.from(new Set([q].concat(words))).sort((a, b) => b.length - a.length);
    }

    function termRegex(terms) {
        if (!terms.length) return null;
        return new RegExp('(' + terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
    }

    /** Escaped HTML of text with the terms wrapped in stabilo marks. */
    function highlight(text, terms) {
        const re = termRegex(terms);
        const safe = esc(text);
        return re ? safe.replace(re, '<mark class="stabilo">$1</mark>') : safe;
    }

    /** Wrap matches in text nodes under el (keeps existing markup intact). */
    function markIn(el, terms) {
        const re = termRegex(terms);
        if (!el || !re) return [];
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
            acceptNode(n) {
                const p = n.parentNode;
                if (!n.nodeValue.trim() || !p || /^(SCRIPT|STYLE|MARK|TEXTAREA|INPUT)$/.test(p.nodeName)) return NodeFilter.FILTER_REJECT;
                re.lastIndex = 0;
                return re.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
            },
        });
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        const marks = [];
        nodes.forEach(node => {
            const frag = document.createDocumentFragment();
            let last = 0;
            node.nodeValue.replace(re, (m, _g, offset) => {
                frag.appendChild(document.createTextNode(node.nodeValue.slice(last, offset)));
                const mark = document.createElement('mark');
                mark.className = 'stabilo';
                mark.textContent = m;
                frag.appendChild(mark);
                marks.push(mark);
                last = offset + m.length;
                return m;
            });
            frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
            node.parentNode.replaceChild(frag, node);
        });
        return marks;
    }

    // ---- visited results ---------------------------------------------------
    function visitedList() {
        try { return JSON.parse(localStorage.getItem(VISITED_KEY) || '[]'); } catch (_) { return []; }
    }
    function cleanUrl(url) {
        try { return new URL(url, location.origin).pathname; } catch (_) { return url; }
    }
    function isVisited(url) { return visitedList().includes(cleanUrl(url)); }
    function markVisited(url) {
        try {
            const list = visitedList().filter(u => u !== cleanUrl(url));
            list.unshift(cleanUrl(url));
            localStorage.setItem(VISITED_KEY, JSON.stringify(list.slice(0, 300)));
        } catch (_) { /* storage blocked — highlight just won't persist */ }
    }
    function withHl(url, query) {
        if (!query) return url;
        return url + (url.includes('?') ? '&' : '?') + 'hl=' + encodeURIComponent(query);
    }

    /** Flash the clicked result with a stabilo mark, remember it, then follow the link. */
    function bindResultClicks(root) {
        root.addEventListener('click', (e) => {
            const a = e.target.closest('a[data-ms-result]');
            if (!a) return;
            markVisited(a.getAttribute('href'));
            a.classList.add('ms-clicked');
        });
    }

    // ---- typeahead box -----------------------------------------------------
    function initBox(opts) {
        injectCss();
        const input = typeof opts.input === 'string' ? document.getElementById(opts.input) : opts.input;
        const dropdown = typeof opts.dropdown === 'string' ? document.getElementById(opts.dropdown) : opts.dropdown;
        const resultsEl = typeof opts.results === 'string' ? document.getElementById(opts.results) : opts.results;
        const wrap = typeof opts.wrap === 'string' ? document.getElementById(opts.wrap) : (opts.wrap || (input && input.parentElement));
        const searchUrl = opts.searchUrl;
        if (!input || !dropdown || !resultsEl) return;

        let timer = null, active = -1, lastQuery = '', controller = null;
        const show = () => { dropdown.style.display = 'block'; dropdown.classList.add('open'); };
        const hide = () => { dropdown.style.display = 'none'; dropdown.classList.remove('open'); active = -1; };
        const goAll = (q) => { location.href = searchUrl + '?q=' + encodeURIComponent(q); };

        function render(data, q) {
            const terms = termsOf(q);
            let html = '';
            const sugg = (data.suggestions || []).filter(s => s.toLowerCase() !== q.toLowerCase()).slice(0, 5);
            if (sugg.length) {
                html += '<div class="ms-section">Saran pencarian</div>';
                html += sugg.map(s => `<a class="ms-item ms-sugg" data-ms-sugg="${esc(s)}" href="${esc(searchUrl + '?q=' + encodeURIComponent(s))}">
                    <span class="ms-icon">🔍</span><div class="ms-body"><div class="ms-title">${highlight(s, terms)}</div></div></a>`).join('');
            }
            if (data.results && data.results.length) {
                html += '<div class="ms-section">Halaman</div>';
                html += data.results.slice(0, 6).map(r => {
                    const visited = isVisited(r.url);
                    const crumb = (r.breadcrumb || []).concat(r.type === 'manual' ? [] : []).join(' › ') || r.manual_title || '';
                    return `<a class="ms-item${visited ? ' ms-visited' : ''}" data-ms-result href="${esc(withHl(r.url, q))}">
                        <span class="ms-icon">${esc(r.icon || r.manual_icon || '📄')}</span>
                        <div class="ms-body">
                            <div class="ms-title">${highlight(r.title, terms)}</div>
                            ${crumb ? `<div class="ms-crumb">${esc(crumb)}</div>` : ''}
                            ${r.subtitle ? `<div class="ms-snippet">${highlight(r.subtitle, terms)}</div>` : ''}
                        </div>
                        ${visited ? '<span class="ms-visited-tag">Dibuka</span>' : ''}
                    </a>`;
                }).join('');
            }
            if (!html) {
                html = `<div class="ms-empty">Tidak ada hasil untuk "<strong>${esc(q)}</strong>".<br>Coba kata kunci lain, misalnya nama menu atau kode proses (FA01, SC04).</div>`;
            } else {
                html += `<div class="ms-footer" data-ms-all><span>Lihat semua ${data.total || data.results.length} hasil untuk "${esc(q)}"</span><kbd>Enter ↵</kbd></div>`;
            }
            resultsEl.innerHTML = html;
            active = -1;
            show();
        }

        async function fetchFor(q) {
            if (controller) controller.abort();
            controller = new AbortController();
            try {
                const res = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: controller.signal,
                });
                const data = await res.json();
                if (input.value.trim() === q) render(data, q);
            } catch (_) { /* aborted or offline */ }
        }

        input.addEventListener('input', () => {
            const q = input.value.trim();
            clearTimeout(timer);
            if (!q) { hide(); return; }
            lastQuery = q;
            timer = setTimeout(() => fetchFor(q), 200);
        });
        input.addEventListener('focus', () => { if (input.value.trim() && resultsEl.innerHTML) show(); });

        input.addEventListener('keydown', (e) => {
            const items = Array.from(resultsEl.querySelectorAll('.ms-item'));
            const setActive = (i) => {
                active = i;
                items.forEach((el, k) => el.classList.toggle('ms-active', k === active));
                if (items[active]) items[active].scrollIntoView({ block: 'nearest' });
            };
            if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) setActive(Math.min(active + 1, items.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) setActive(Math.max(active - 1, -1)); }
            else if (e.key === 'Enter') {
                e.preventDefault();
                const el = items[active];
                if (el) { if (el.hasAttribute('data-ms-result')) markVisited(el.getAttribute('href')); location.href = el.href; }
                else if (input.value.trim()) goAll(input.value.trim());
            } else if (e.key === 'Escape') { hide(); input.blur(); }
        });

        resultsEl.addEventListener('click', (e) => {
            if (e.target.closest('[data-ms-all]')) { goAll(input.value.trim() || lastQuery); }
        });
        bindResultClicks(resultsEl);
        document.addEventListener('click', (e) => { if (wrap && !wrap.contains(e.target)) hide(); });
    }

    // ---- in-page highlight (viewer opened from a result) ---------------------
    function highlightPage(container, query) {
        injectCss();
        const terms = termsOf(query);
        const marks = markIn(container, terms);
        if (!marks.length) return;
        let idx = 0;
        const bar = document.createElement('div');
        bar.className = 'ms-hlbar';
        bar.setAttribute('role', 'status');
        bar.innerHTML = `<span>🖍️ Menyorot <b>"${esc(query)}"</b></span><span class="ms-count"></span>
            <button type="button" data-dir="-1" title="Sebelumnya" aria-label="Sebelumnya">▲</button>
            <button type="button" data-dir="1" title="Berikutnya" aria-label="Berikutnya">▼</button>
            <button type="button" data-close title="Hapus sorotan" aria-label="Hapus sorotan">✕</button>`;
        document.body.appendChild(bar);
        const count = bar.querySelector('.ms-count');
        function go(i) {
            marks[idx].classList.remove('stabilo-active');
            idx = (i + marks.length) % marks.length;
            marks[idx].classList.add('stabilo-active');
            marks[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
            count.textContent = `${idx + 1} / ${marks.length}`;
        }
        bar.addEventListener('click', (e) => {
            const b = e.target.closest('button');
            if (!b) return;
            if (b.hasAttribute('data-close')) {
                marks.forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
                container.normalize();
                bar.remove();
                const url = new URL(location.href);
                url.searchParams.delete('hl');
                history.replaceState(null, '', url.pathname + url.search + url.hash);
                return;
            }
            go(idx + parseInt(b.dataset.dir, 10));
        });
        setTimeout(() => go(0), 120);
        markVisited(location.pathname);
    }

    window.ManualSearch = { initBox, highlight, markIn, highlightPage, termsOf, withHl, markVisited, isVisited, bindResultClicks, injectCss, esc };
})();
