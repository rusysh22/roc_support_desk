/**
 * Live-typing dropdown for the Knowledge Base search box(es).
 * Debounces input, calls the kb_search_suggest JSON endpoint, and renders
 * matches with the query highlighted in <mark> — used on both the KB home
 * hero search and the search-results page search box.
 */
(function () {
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function highlight(text, query) {
    const escaped = escapeHtml(text || '');
    const words = query.trim().split(/\s+/).filter(Boolean).map(escapeRegExp);
    if (!words.length) return escaped;
    const re = new RegExp('(' + words.join('|') + ')', 'ig');
    return escaped.replace(re, '<mark>$1</mark>');
  }

  function initKbSearchSuggest(inputId, dropdownId, suggestUrl) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    let timer = null;
    let requestSeq = 0;

    function hide() {
      dropdown.classList.add('hidden');
      dropdown.innerHTML = '';
    }

    function render(results, query) {
      if (!results.length) { hide(); return; }
      dropdown.innerHTML = results.map(r => (
        '<a href="' + r.url + '" class="block px-4 py-2.5 hover:bg-indigo-50 transition-colors border-b border-slate-100 last:border-b-0">' +
          '<div class="text-sm font-semibold text-slate-800 truncate">' + highlight(r.title, query) + '</div>' +
          (r.summary ? '<div class="text-xs text-slate-500 truncate mt-0.5">' + highlight(r.summary, query) + '</div>' : '') +
        '</a>'
      )).join('');
      dropdown.classList.remove('hidden');
    }

    function fetchSuggestions(q) {
      const seq = ++requestSeq;
      fetch(suggestUrl + '?q=' + encodeURIComponent(q))
        .then(r => r.json())
        .then(data => {
          if (seq !== requestSeq) return; // a newer keystroke already superseded this request
          render(data.results || [], q);
        })
        .catch(() => {});
    }

    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { hide(); return; }
      timer = setTimeout(() => fetchSuggestions(q), 250);
    });

    input.addEventListener('focus', () => {
      if (dropdown.innerHTML && input.value.trim().length >= 2) dropdown.classList.remove('hidden');
    });

    document.addEventListener('click', (e) => {
      if (e.target !== input && !dropdown.contains(e.target)) hide();
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hide();
    });
  }

  window.initKbSearchSuggest = initKbSearchSuggest;
})();
