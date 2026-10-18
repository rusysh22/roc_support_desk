/*
 * Category drill-down picker.
 *
 * Turns <select data-category-cascade> (options carry data-key / data-parent / data-leaf) into ONE dropdown whose
 * panel moves level by level: click a group and the panel slides to its sub-categories, with a "Back" row to go up.
 * A group shows a chevron; a final category shows a small dot icon and is the only thing that sets a value.
 * The original <select> stays in the page (hidden) and holds the value, so forms, onchange handlers, htmx
 * filter forms and the unsaved-changes guard keep working.
 *
 *   data-allow-group  filters: the sub-level also offers "All in <group>", and the top level "All categories".
 *   otherwise         only a final category can be chosen.
 *
 * Pages that refresh themselves (the ticket lists swap only their rows; the chat swaps only the thread) never
 * replace this control. If a swap does bring a new select in, htmx:load builds it again.
 */
(function () {
  'use strict';

  var CHEVRON = '<svg class="cc-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M7.5 4.5 13 10l-5.5 5.5"/></svg>';
  var BACK = '<svg class="cc-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12.5 4.5 7 10l5.5 5.5"/></svg>';
  var CARET = '<svg class="cc-caret" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="m5 8 5 5 5-5"/></svg>';
  var TAG = '<svg class="cc-ico cc-leaf" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><circle cx="10" cy="10" r="4.5"/></svg>';
  var CHECK = '<svg class="cc-ico cc-check" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 10.5 3.5 3.5 7.5-8"/></svg>';

  var style = document.createElement('style');
  style.textContent =
    '.cc{position:relative;min-width:0}' +
    // background-image:none drops the chevron that select classes (e.g. .jk-select) draw; the trigger has its own caret
    '.cc-trigger{display:flex;align-items:center;gap:.4rem;width:100%;text-align:left;cursor:pointer;background-color:#fff;background-image:none !important;}' +
    '.cc-trigger .cc-text{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.cc-trigger .cc-text.is-empty{color:#64748b}' +
    '.cc-caret{width:1rem;height:1rem;flex:none;color:#64748b}' +
    '.cc-panel{position:fixed;z-index:10000;background:#fff;border:1px solid #cbd5e1;border-radius:.75rem;box-shadow:0 12px 32px rgba(15,23,42,.18);overflow:hidden;font-size:.85rem;color:#0f172a;display:flex;flex-direction:column}' +
    '.cc-panel[hidden]{display:none}' +
    '.cc-head{display:flex;align-items:center;gap:.4rem;padding:.55rem .7rem;border-bottom:1px solid #e2e8f0;background:#f8fafc;font-weight:700;color:#1e293b}' +
    '.cc-head button{display:inline-flex;align-items:center;gap:.2rem;border:0;background:#e0e7ff;color:#3730a3;border-radius:.5rem;padding:.2rem .55rem .2rem .3rem;font-weight:700;font-size:.8rem;cursor:pointer}' +
    '.cc-head button:hover{background:#c7d2fe}' +
    '.cc-head span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.cc-view{overflow-y:auto;overscroll-behavior:contain;padding:.25rem}' +
    '.cc-view.fwd{animation:cc-fwd .18s ease-out}.cc-view.back{animation:cc-back .18s ease-out}' +
    '@keyframes cc-fwd{from{transform:translateX(28px);opacity:.2}to{transform:none;opacity:1}}' +
    '@keyframes cc-back{from{transform:translateX(-28px);opacity:.2}to{transform:none;opacity:1}}' +
    '.cc-item{display:flex;align-items:center;gap:.5rem;width:100%;text-align:left;border:0;background:transparent;padding:.5rem .6rem;border-radius:.5rem;cursor:pointer;color:inherit;font:inherit}' +
    '.cc-item:hover,.cc-item:focus-visible{background:#eef2ff;outline:none}' +
    '.cc-item .cc-name{flex:1 1 auto;min-width:0;overflow-wrap:anywhere}' +
    '.cc-item.is-selected{background:#ecfdf5;font-weight:700}' +
    '.cc-item.is-all{color:#4338ca;font-weight:600}' +
    '.cc-ico{width:1rem;height:1rem;flex:none}' +
    '.cc-leaf{color:#059669}.cc-group .cc-ico{color:#64748b}.cc-check{color:#059669}' +
    '.cc-empty{padding:.7rem;color:#64748b}';
  document.head.appendChild(style);

  function build(select) {
    if (select.dataset.cascadeReady) return;
    select.dataset.cascadeReady = '1';

    var allowGroup = select.hasAttribute('data-allow-group');
    var nodes = {}, kids = {}, roots = [], placeholder = '';
    Array.prototype.forEach.call(select.options, function (o) {
      if (!o.dataset.key) { if (o.value === '') placeholder = o.textContent.trim(); return; }
      nodes[o.dataset.key] = { key: o.dataset.key, parent: o.dataset.parent || '', leaf: o.dataset.leaf === '1', name: o.textContent.trim(), value: o.value };
    });
    Object.keys(nodes).forEach(function (k) {
      var n = nodes[k];
      if (n.parent && nodes[n.parent]) (kids[n.parent] = kids[n.parent] || []).push(n); else roots.push(n);
    });

    var wrap = document.createElement('div');
    wrap.className = 'cc';
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = select.className + ' cc-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    var text = document.createElement('span');
    text.className = 'cc-text';
    trigger.appendChild(text);
    trigger.insertAdjacentHTML('beforeend', CARET);
    var panel = document.createElement('div');
    panel.className = 'cc-panel';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Choose a category');
    wrap.appendChild(trigger);
    wrap.appendChild(panel);
    select.insertAdjacentElement('afterend', wrap);
    select.style.display = 'none';
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;

    var level = '';            // key of the group being shown ('' = top level)
    var open = false;

    function selectedNode() {
      var o = select.options[select.selectedIndex];
      return o && o.dataset.key ? nodes[o.dataset.key] : null;
    }
    function trail(node) {
      var names = [];
      for (var n = node; n; n = nodes[n.parent]) names.unshift(n.name);
      return names;
    }
    function syncTrigger() {
      var node = selectedNode();
      if (node) {
        text.textContent = trail(node).join(' › ');
        text.classList.remove('is-empty');
        trigger.title = text.textContent;
      } else {
        text.textContent = placeholder || 'Select category…';
        text.classList.add('is-empty');
        trigger.removeAttribute('title');
      }
    }

    function setValue(value) {
      if (select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      syncTrigger();
    }

    function item(cls, html, onclick) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'cc-item ' + cls;
      b.innerHTML = html;
      b.addEventListener('click', onclick);
      return b;
    }
    function esc(t) { var d = document.createElement('span'); d.textContent = t; return d.innerHTML; }

    function render(direction) {
      panel.innerHTML = '';
      var group = level ? nodes[level] : null;
      if (group) {
        var head = document.createElement('div');
        head.className = 'cc-head';
        var back = document.createElement('button');
        back.type = 'button';
        back.innerHTML = BACK + 'Back';
        back.addEventListener('click', function () { level = group.parent && nodes[group.parent] ? group.parent : ''; render('back'); focusFirst(); });
        var title = document.createElement('span');
        title.textContent = group.name;
        head.appendChild(back);
        head.appendChild(title);
        panel.appendChild(head);
      }
      var view = document.createElement('div');
      view.className = 'cc-view' + (direction ? ' ' + direction : '');
      var cur = selectedNode();
      var curValue = cur ? cur.value : '';

      if (allowGroup) {
        var allText = group ? 'All in ' + group.name : (placeholder || 'All categories');
        var allValue = group ? group.value : '';
        view.appendChild(item('is-all' + (curValue === allValue ? ' is-selected' : ''),
          '<span class="cc-name">' + esc(allText) + '</span>' + (curValue === allValue ? CHECK : ''),
          function () { setValue(allValue); close(true); }));
      }
      (group ? kids[group.key] || [] : roots).forEach(function (n) {
        var isGroup = !!kids[n.key];
        var selected = !isGroup && n.value === curValue;
        var el = item((isGroup ? 'cc-group' : 'cc-final') + (selected ? ' is-selected' : ''),
          '<span class="cc-name">' + esc(n.name) + '</span>' + (isGroup ? CHEVRON : (selected ? CHECK : TAG)),
          function () {
            if (isGroup) { level = n.key; render('fwd'); focusFirst(); }
            else { setValue(n.value); close(true); }
          });
        el.dataset.key = n.key;
        el.setAttribute('aria-label', n.name + (isGroup ? ', has sub-categories' : ''));
        view.appendChild(el);
      });
      if (!view.children.length) { var e = document.createElement('div'); e.className = 'cc-empty'; e.textContent = 'No categories.'; view.appendChild(e); }
      panel.appendChild(view);
      place();
    }

    function focusFirst() {
      var first = panel.querySelector('.cc-item.is-selected') || panel.querySelector('.cc-item');
      if (first) first.focus({ preventScroll: true });
    }

    function place() {
      var r = trigger.getBoundingClientRect();
      var width = Math.max(r.width, 272);
      var left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
      var below = window.innerHeight - r.bottom - 12, above = r.top - 12;
      var up = below < 240 && above > below;
      var max = Math.max(180, Math.min(380, up ? above : below));
      panel.style.width = width + 'px';
      panel.style.left = left + 'px';
      panel.style.maxHeight = max + 'px';
      panel.querySelector('.cc-view').style.maxHeight = (max - (level ? 46 : 0)) + 'px';
      if (up) { panel.style.top = 'auto'; panel.style.bottom = (window.innerHeight - r.top + 4) + 'px'; }
      else { panel.style.bottom = 'auto'; panel.style.top = (r.bottom + 4) + 'px'; }
    }

    function openPanel() {
      var cur = selectedNode();
      level = cur && cur.parent && nodes[cur.parent] ? cur.parent : '';   // show the selection's siblings
      if (allowGroup && cur && !cur.leaf && kids[cur.key]) level = cur.key;
      open = true;
      panel.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      window._chatInteracting = true;      // the ticket chat pauses its 5 s poll while a control is in use
      render();
      focusFirst();
    }
    function close(refocus) {
      if (!open) return;
      open = false;
      panel.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      window._chatInteracting = false;
      if (refocus) trigger.focus({ preventScroll: true });
    }

    trigger.addEventListener('click', function () { open ? close(false) : openPanel(); });
    panel.addEventListener('keydown', function (e) {
      var items = Array.prototype.slice.call(panel.querySelectorAll('.cc-item')), i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === 'ArrowRight' && document.activeElement.classList.contains('cc-group')) { e.preventDefault(); document.activeElement.click(); }
      else if ((e.key === 'ArrowLeft' || e.key === 'Backspace') && level) { e.preventDefault(); var g = nodes[level]; level = g.parent && nodes[g.parent] ? g.parent : ''; render('back'); focusFirst(); }
    });
    wrap.addEventListener('keydown', function (e) { if (e.key === 'Escape' && open) { e.stopPropagation(); close(true); } });
    trigger.addEventListener('keydown', function (e) { if (e.key === 'ArrowDown' && !open) { e.preventDefault(); openPanel(); } });
    document.addEventListener('mousedown', function (e) { if (open && !wrap.contains(e.target)) close(false); });
    window.addEventListener('resize', function () { if (open) place(); });
    window.addEventListener('scroll', function (e) { if (open && !panel.contains(e.target)) close(false); }, true);
    // the page may change the value itself (form reset, saved filters)
    select.addEventListener('change', syncTrigger);
    var form = select.form;
    if (form) form.addEventListener('reset', function () { setTimeout(syncTrigger, 0); });

    syncTrigger();
  }

  function init(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('select[data-category-cascade]'), build);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(); });
  else init();
  document.addEventListener('htmx:load', function (e) { init(e.target); });
  window.initCategoryCascade = init;
})();
