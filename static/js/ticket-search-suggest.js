/**
 * "Recently accessed" + live-search dropdown for portal ticket pickers —
 * used on the My Tickets search bar and the in-ticket switcher. Mirrors the
 * conventions of kb-search-suggest.js: plain JS, no framework dependency.
 *
 * Recent tickets are tracked client-side in localStorage (same pattern as
 * the sidebar's "jk_recent_pages"), then re-hydrated with live subject/status
 * from the server via `?ids=` so the dropdown never shows stale data.
 */
(function () {
  var RECENT_KEY = 'jk_recent_tickets';
  var RECENT_LIMIT = 8;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var STATUS_CLASS = {
    amber: 'bg-amber-100 text-amber-800',
    red: 'bg-red-100 text-red-700',
    yellow: 'bg-amber-100 text-amber-700',
    blue: 'bg-indigo-100 text-indigo-700',
    indigo: 'bg-indigo-100 text-indigo-700',
    orange: 'bg-orange-100 text-orange-700',
    green: 'bg-emerald-100 text-emerald-700',
    pink: 'bg-pink-100 text-pink-700',
    slate: 'bg-slate-100 text-slate-500',
  };

  function recordRecentTicket(id, caseNumber, subject) {
    try {
      var recents = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      recents = recents.filter(function (r) { return r.id !== id; });
      recents.unshift({ id: id, case_number: caseNumber, subject: subject });
      localStorage.setItem(RECENT_KEY, JSON.stringify(recents.slice(0, RECENT_LIMIT)));
    } catch (e) {}
  }

  function readRecentIds() {
    try {
      var recents = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return recents.map(function (r) { return r.id; }).filter(Boolean);
    } catch (e) { return []; }
  }

  function initTicketSearchSuggest(inputId, dropdownId, searchUrl) {
    var input = document.getElementById(inputId);
    var dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    var timer = null;
    var requestSeq = 0;

    function hide() {
      dropdown.classList.add('hidden');
      dropdown.innerHTML = '';
    }

    function render(results, label) {
      if (!results.length) {
        dropdown.innerHTML = '<div class="px-3.5 py-2.5 text-xs text-slate-400">' + escapeHtml(label.empty) + '</div>';
        dropdown.classList.remove('hidden');
        return;
      }
      var html = '<div class="px-3.5 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">' + escapeHtml(label.header) + '</div>';
      html += results.map(function (r) {
        var badgeClass = STATUS_CLASS[r.status_color] || STATUS_CLASS.slate;
        return (
          '<a href="' + r.url + '" class="flex items-center gap-2.5 px-3.5 py-2 hover:bg-slate-50 transition-colors">' +
            '<span class="font-mono text-[10px] font-bold text-slate-400 flex-shrink-0">' + escapeHtml(r.case_number) + '</span>' +
            '<span class="text-sm text-slate-700 truncate flex-1 min-w-0">' + escapeHtml(r.subject) + '</span>' +
            '<span class="text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0 ' + badgeClass + '">' + escapeHtml(r.status_label) + '</span>' +
          '</a>'
        );
      }).join('');
      dropdown.innerHTML = html;
      dropdown.classList.remove('hidden');
    }

    function showRecent() {
      var ids = readRecentIds();
      if (!ids.length) {
        render([], { header: 'Recently Accessed', empty: 'Belum ada tiket yang baru-baru ini dibuka.' });
        return;
      }
      var seq = ++requestSeq;
      fetch(searchUrl + '?ids=' + encodeURIComponent(ids.join(',')))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (seq !== requestSeq) return;
          render(data.results || [], { header: 'Recently Accessed', empty: 'Belum ada tiket yang baru-baru ini dibuka.' });
        })
        .catch(function () {});
    }

    function runSearch(q) {
      var seq = ++requestSeq;
      fetch(searchUrl + '?q=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (seq !== requestSeq) return;
          render(data.results || [], { header: 'Hasil Pencarian', empty: 'Tidak ada tiket yang cocok.' });
        })
        .catch(function () {});
    }

    input.addEventListener('focus', function () {
      var q = input.value.trim();
      if (q) runSearch(q); else showRecent();
    });

    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (!q) { showRecent(); return; }
      timer = setTimeout(function () { runSearch(q); }, 250);
    });

    document.addEventListener('click', function (e) {
      if (e.target !== input && !dropdown.contains(e.target)) hide();
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { hide(); input.blur(); }
    });
  }

  window.initTicketSearchSuggest = initTicketSearchSuggest;
  window.recordRecentTicket = recordRecentTicket;
})();
