/* Search the fully loaded conversation, including messages arriving over WS/polling. */
(function () {
  'use strict';
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
  const fold = value => normalize(value).normalize('NFKC').toLocaleLowerCase();

  function collectMessages(thread) {
    const seen = new Set();
    return Array.from(thread.querySelectorAll('#chat-history [data-msg-id], #chat-live [data-msg-id]'))
      .reverse().flatMap(element => {
        const id = element.dataset.msgId;
        if (seen.has(id)) return [];
        seen.add(id);
        if (element.dataset.searchDeleted === 'true') return [];
        let text = element.dataset.searchText;
        if (text === undefined) {
          const content = (element.querySelector('.bubble-content') || element).cloneNode(true);
          content.querySelectorAll('.quote-block, .reply-actions, .read-status, script, style, svg, audio, video').forEach(node => node.remove());
          content.querySelectorAll('br').forEach(node => node.replaceWith(' '));
          content.querySelectorAll('p, div, li, pre').forEach(node => node.append(' '));
          text = content.textContent;
        }
        return [{ id, element, text: normalize(text), sender: element.dataset.searchSender || 'Message', time: element.dataset.searchTime || '' }];
      });
  }

  function findMatches(records, query) {
    const term = fold(query);
    return term ? records.filter(record => fold(record.text).includes(term)) : [];
  }

  function init() {
    const byId = id => document.getElementById(id);
    const toggle = byId('chat-search-toggle');
    const panel = byId('chat-message-search');
    const input = byId('chat-message-search-input');
    const results = byId('chat-message-search-results');
    const status = byId('chat-message-search-status');
    const previous = byId('chat-message-search-prev');
    const next = byId('chat-message-search-next');
    const close = byId('chat-message-search-close');
    const thread = byId('chat-scroll');
    if (!toggle || !panel || !thread) return;
    let matches = [], selected = -1, timer;

    function clearHighlight() {
      thread.querySelectorAll('.chat-search-active').forEach(element => element.classList.remove('chat-search-active'));
    }

    function updateStatus() {
      const term = normalize(input.value);
      status.textContent = !term ? "Search within this ticket's messages." : !matches.length ? 'No messages found in this ticket.' :
        (selected >= 0 ? `${selected + 1} of ${matches.length} matching messages` : `${matches.length} matching message${matches.length === 1 ? '' : 's'}`);
      previous.disabled = next.disabled = !matches.length;
    }

    function select(index) {
      if (!matches.length) return;
      selected = (index + matches.length) % matches.length;
      clearHighlight();
      const match = matches[selected];
      match.element.classList.add('chat-search-active');
      if (typeof window.chatJumpToMessage === 'function') window.chatJumpToMessage(match.id);
      results.querySelectorAll('button').forEach(button => button.setAttribute('aria-current', String(button.dataset.messageId === match.id)));
      updateStatus();
    }

    function appendSnippet(button, text) {
      // Build highlights using text nodes so message HTML never executes in results.
      const term = normalize(input.value);
      const at = text.toLocaleLowerCase().indexOf(term.toLocaleLowerCase());
      const start = Math.max(0, at - 45);
      const end = Math.min(text.length, Math.max(start + 150, at + term.length));
      button.append(start ? '...' : '');
      if (at >= 0) {
        button.append(text.slice(start, at));
        const mark = document.createElement('mark');
        mark.textContent = text.slice(at, at + term.length);
        button.append(mark, text.slice(at + term.length, end));
      } else {
        button.append(text.slice(start, end));
      }
      if (end < text.length) button.append('...');
    }

    function search() {
      const selectedId = matches[selected]?.id;
      matches = findMatches(collectMessages(thread), input.value);
      selected = matches.findIndex(match => match.id === selectedId);
      clearHighlight();
      results.replaceChildren();
      matches.slice(0, 50).forEach((match, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-search-result';
        button.dataset.messageId = match.id;
        button.setAttribute('aria-current', String(index === selected));
        const meta = document.createElement('small');
        meta.textContent = [match.sender, match.time].filter(Boolean).join(' - ');
        button.append(meta);
        appendSnippet(button, match.text);
        button.addEventListener('click', () => select(index));
        results.append(button);
      });
      if (matches.length > 50) {
        const note = document.createElement('p');
        note.textContent = 'Showing the latest 50 matches. Use the arrows to navigate all matches.';
        note.className = 'text-xs text-slate-500';
        results.append(note);
      }
      if (selected >= 0) matches[selected].element.classList.add('chat-search-active');
      updateStatus();
    }

    function closeSearch() {
      clearTimeout(timer);
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      input.value = '';
      search();
      toggle.focus();
    }
    toggle.addEventListener('click', () => {
      if (!panel.hidden) { closeSearch(); return; }
      panel.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      input.focus();
      search();
    });
    close.addEventListener('click', closeSearch);
    input.addEventListener('input', () => {
      selected = -1;
      clearTimeout(timer);
      timer = setTimeout(search, 150);
    });
    panel.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if (event.key === 'Escape') { event.preventDefault(); closeSearch(); }
      if (event.key === 'Enter' && event.target === input) {
        event.preventDefault();
        clearTimeout(timer);
        search();
        select(selected < 0 ? 0 : selected + (event.shiftKey ? -1 : 1));
      }
    });
    previous.addEventListener('click', () => select(selected < 0 ? 0 : selected - 1));
    next.addEventListener('click', () => select(selected + 1));
    const observer = new MutationObserver(() => {
      if (!panel.hidden && normalize(input.value)) { clearTimeout(timer); timer = setTimeout(search, 150); }
    });
    observer.observe(thread, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['data-search-deleted'] });
    return { search, select, closeSearch };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { collectMessages, findMatches, init };
  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
})();
