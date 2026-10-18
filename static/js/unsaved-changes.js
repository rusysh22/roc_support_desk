/*
 * Unsaved-changes guard for edit forms.
 *
 * Opt in by adding `data-unsaved-guard` to a <form>. When the user has changed
 * that form and tries to leave the page, a "Discard unsaved changes?" dialog
 * asks before the changes are lost:
 *   - in-app links, Back buttons ([data-jk-back]) and anything marked
 *     [data-unsaved-confirm] get the app dialog ([data-unsaved-ignore] opts a
 *     link out, e.g. one that only opens a modal);
 *   - submitting a *different* form that would reload the page (e.g. Delete,
 *     Publish) gets the app dialog;
 *   - reload by keyboard (F5, Ctrl+R) and the browser Back button get it too;
 *   - only the browser's Reload button, closing the tab and typing a URL fall
 *     back to the browser's own dialog (pages cannot intercept those).
 *
 * A form counts as changed when its values differ from what they were when the
 * user first touched it, so values set by page scripts on load never count.
 * A form re-rendered by the server with validation errors counts as changed
 * from the start, because what it shows has not been saved yet.
 * `data-unsaved-guard="dirty"` forces that state too.
 *
 * Saving the form (normal submit, form.submit(), or a successful htmx request)
 * clears the state. Exposed as window.jkUnsaved for page scripts.
 */
(function () {
    if (window.jkUnsaved) return;

    var GUARD = '[data-unsaved-guard]';
    // Server-rendered errors only; client-side hints mark theirs with data-client-error.
    var SERVER_ERRORS = '.jk-form-error:not([data-client-error]), .errorlist, [data-form-errors]';
    var SKIP_TYPES = { submit: 1, button: 1, reset: 1, image: 1 };

    var baselines = new WeakMap(); // form -> snapshot taken on first user interaction
    var forced = new WeakSet();    // forms shown with server errors (unsaved by definition)
    var cleared = new WeakSet();   // forms saved or discarded since

    function guardedForm(el) {
        if (!el || el.nodeType !== 1) return null;
        var form = el.form || (el.closest && el.closest('form'));
        return form && form.matches(GUARD) ? form : null;
    }

    function snapshot(form) {
        var parts = [];
        Array.prototype.forEach.call(form.elements, function (el) {
            if (!el.name || el.disabled || SKIP_TYPES[el.type] || el.name === 'csrfmiddlewaretoken') return;
            var value;
            if (el.type === 'checkbox' || el.type === 'radio') value = el.checked ? el.value : '';
            else if (el.type === 'file') value = Array.prototype.map.call(el.files || [], function (f) { return f.name + ':' + f.size; }).join('|');
            else if (el.tagName === 'SELECT' && el.multiple) value = Array.prototype.map.call(el.selectedOptions, function (o) { return o.value; }).join('|');
            else value = el.value;
            parts.push(el.name + '=' + value);
        });
        // Rich-text editors (Quill) only copy into their hidden field on submit.
        form.querySelectorAll('[contenteditable]:not([contenteditable="false"])').forEach(function (el) {
            parts.push('#editor=' + el.innerHTML);
        });
        return parts.join('\u0000');
    }

    // An error the server rendered: it has text and is not driven by Alpine. Client-side hints
    // (e.g. <p class="jk-form-error" x-show="picker.error">) sit in the page empty and hidden.
    function showsServerErrors(form) {
        return Array.prototype.some.call(form.querySelectorAll(SERVER_ERRORS), function (el) {
            if (el.hasAttribute('x-show') || el.hasAttribute('x-text') || el.hasAttribute('x-html')) return false;
            return (el.textContent || '').trim() !== '';
        });
    }

    function noteServerErrors(form) {
        if (!cleared.has(form) && showsServerErrors(form)) forced.add(form);
    }

    function isDirty(form) {
        if (!form.isConnected) return false;
        noteServerErrors(form);
        if (!cleared.has(form) && (forced.has(form) || form.getAttribute('data-unsaved-guard') === 'dirty')) return true;
        var base = baselines.get(form);
        return base !== undefined && base !== snapshot(form);
    }

    function dirtyForms() {
        return Array.prototype.filter.call(document.querySelectorAll('form' + GUARD), isDirty);
    }

    function markClean(form) {
        cleared.add(form);
        forced.delete(form);
        baselines.set(form, snapshot(form));
    }

    // Baseline = the form as it was right before the user first touched it.
    function track(e) {
        if (!e.isTrusted) return;
        var form = guardedForm(e.target);
        if (!form || baselines.has(form)) return;
        noteServerErrors(form);
        baselines.set(form, snapshot(form));
    }
    ['focusin', 'pointerdown', 'keydown', 'dragenter'].forEach(function (type) {
        document.addEventListener(type, track, true);
    });

    // ---- Dialog -------------------------------------------------------------
    var STYLE = '' +
        '.jk-unsaved-backdrop{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(15,23,42,.6);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);animation:jk-unsaved-fade .12s ease-out}' +
        '.jk-unsaved-dialog{width:100%;max-width:440px;background:#fff;border-radius:16px;box-shadow:0 25px 50px -12px rgba(0,0,0,.35);overflow:hidden;font-family:Inter,system-ui,sans-serif;animation:jk-unsaved-pop .14s ease-out}' +
        '.jk-unsaved-body{display:flex;gap:14px;padding:22px 22px 6px}' +
        '.jk-unsaved-icon{flex-shrink:0;width:40px;height:40px;border-radius:9999px;background:#fef3c7;color:#d97706;display:flex;align-items:center;justify-content:center}' +
        '.jk-unsaved-icon svg{width:22px;height:22px}' +
        '.jk-unsaved-title{margin:0;font-size:16px;font-weight:700;color:#1e293b;line-height:1.4}' +
        '.jk-unsaved-text{margin:6px 0 0;font-size:14px;color:#475569;line-height:1.5}' +
        '.jk-unsaved-actions{display:flex;justify-content:flex-end;gap:10px;padding:18px 22px 20px}' +
        '.jk-unsaved-btn{border:1px solid transparent;border-radius:10px;padding:8px 16px;font-size:14px;font-weight:600;cursor:pointer;transition:background-color .15s,border-color .15s}' +
        '.jk-unsaved-btn:focus-visible{outline:2px solid #4f46e5;outline-offset:2px}' +
        '.jk-unsaved-keep{background:#fff;border-color:#cbd5e1;color:#334155}.jk-unsaved-keep:hover{background:#f8fafc}' +
        '.jk-unsaved-discard{background:#dc2626;color:#fff}.jk-unsaved-discard:hover{background:#b91c1c}' +
        '@keyframes jk-unsaved-fade{from{opacity:0}to{opacity:1}}' +
        '@keyframes jk-unsaved-pop{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:scale(1)}}';

    var openDialog = null;

    function confirmDiscard(onDiscard) {
        if (openDialog) return;
        if (!document.getElementById('jk-unsaved-style')) {
            var style = document.createElement('style');
            style.id = 'jk-unsaved-style';
            style.textContent = STYLE;
            document.head.appendChild(style);
        }
        var previousFocus = document.activeElement;
        var backdrop = document.createElement('div');
        backdrop.className = 'jk-unsaved-backdrop';
        backdrop.innerHTML =
            '<div class="jk-unsaved-dialog" role="alertdialog" aria-modal="true" aria-labelledby="jk-unsaved-title" aria-describedby="jk-unsaved-text">' +
                '<div class="jk-unsaved-body">' +
                    '<div class="jk-unsaved-icon"><svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg></div>' +
                    '<div>' +
                        '<h3 class="jk-unsaved-title" id="jk-unsaved-title">Discard unsaved changes?</h3>' +
                        '<p class="jk-unsaved-text" id="jk-unsaved-text">You have changes on this page that have not been saved. If you leave now, they will be lost.</p>' +
                    '</div>' +
                '</div>' +
                '<div class="jk-unsaved-actions">' +
                    '<button type="button" class="jk-unsaved-btn jk-unsaved-keep">Keep editing</button>' +
                    '<button type="button" class="jk-unsaved-btn jk-unsaved-discard">Discard changes</button>' +
                '</div>' +
            '</div>';

        function close() {
            document.removeEventListener('keydown', onKey, true);
            backdrop.remove();
            openDialog = null;
            if (previousFocus && previousFocus.focus) previousFocus.focus();
        }
        function onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
            else if (e.key === 'Tab') { // keep focus inside the two buttons
                var keep = backdrop.querySelector('.jk-unsaved-keep'), discard = backdrop.querySelector('.jk-unsaved-discard');
                e.preventDefault();
                (document.activeElement === keep ? discard : keep).focus();
            }
        }
        backdrop.addEventListener('click', function (e) { if (e.target === backdrop) close(); });
        backdrop.querySelector('.jk-unsaved-keep').addEventListener('click', close);
        backdrop.querySelector('.jk-unsaved-discard').addEventListener('click', function () {
            dirtyForms().forEach(markClean);
            close();
            onDiscard();
        });
        document.addEventListener('keydown', onKey, true);
        document.body.appendChild(backdrop);
        openDialog = backdrop;
        backdrop.querySelector('.jk-unsaved-keep').focus();
    }

    // ---- Leaving through a link or Back button --------------------------------
    function leavesPage(el) {
        if (el.matches('[data-jk-back], [data-unsaved-confirm]')) return true;
        if (el.target && el.target !== '_self') return false;
        if (el.hasAttribute('download')) return false;
        var href = el.getAttribute('href');
        return !!href && href.charAt(0) !== '#' && !/^(javascript|mailto|tel):/i.test(href);
    }

    window.addEventListener('click', function (e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.defaultPrevented) return;
        var el = e.target.closest && e.target.closest('a[href], [data-jk-back], [data-unsaved-confirm]');
        if (!el || !leavesPage(el) || el.closest('[data-unsaved-ignore]') || !dirtyForms().length) return;
        // Capture phase on window: runs before htmx-boost / Alpine / back-button handlers.
        e.preventDefault();
        e.stopImmediatePropagation();
        confirmDiscard(function () { el.click(); });
    }, true);

    // ---- Leaving by submitting another form -----------------------------------
    var nativeSubmit = HTMLFormElement.prototype.submit;

    function isHtmxForm(form) {
        return ['hx-post', 'hx-put', 'hx-patch', 'hx-get', 'hx-delete', 'data-hx-post'].some(function (a) { return form.hasAttribute(a); });
    }

    // Returns true when the submit must wait for the dialog (other forms have unsaved changes).
    function holdSubmit(form, proceed) {
        if (form.target && form.target !== '_self') return false; // page stays
        var others = dirtyForms().filter(function (f) { return f !== form; });
        if (!others.length) { // nothing else to lose; saving this form clears it
            if (form.matches(GUARD)) markClean(form);
            return false;
        }
        confirmDiscard(proceed);
        return true;
    }

    window.addEventListener('submit', function (e) {
        var form = e.target;
        if (e.defaultPrevented || !(form instanceof HTMLFormElement) || isHtmxForm(form)) return;
        var submitter = e.submitter;
        if (holdSubmit(form, function () {
            if (submitter && submitter.name) { // keep the clicked button's name/value
                var hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = submitter.name;
                hidden.value = submitter.value;
                form.appendChild(hidden);
            }
            nativeSubmit.call(form);
        })) e.preventDefault();
    });

    // form.submit() fires no submit event, so page scripts calling it go through here.
    HTMLFormElement.prototype.submit = function () {
        var form = this;
        if (holdSubmit(form, function () { nativeSubmit.call(form); })) return;
        return nativeSubmit.call(form);
    };

    // A successful htmx save often answers with a redirect/refresh header and the browser
    // leaves at once, so mark the form saved before htmx acts on it.
    document.addEventListener('htmx:beforeOnLoad', function (e) {
        var form = guardedForm(e.detail && e.detail.elt), xhr = e.detail && e.detail.xhr;
        if (!form || !xhr || xhr.status < 200 || xhr.status >= 300) return;
        if (xhr.getResponseHeader('HX-Redirect') || xhr.getResponseHeader('HX-Location') || xhr.getResponseHeader('HX-Refresh')) markClean(form);
    });

    // htmx-submitted guarded forms are saved once the request succeeds.
    document.addEventListener('htmx:afterRequest', function (e) {
        var form = guardedForm(e.detail && e.detail.elt);
        if (form && e.detail.successful && form.isConnected && !showsServerErrors(form)) markClean(form);
    });

    // ---- Reload by keyboard (F5, Ctrl/Cmd+R) ------------------------------------
    document.addEventListener('keydown', function (e) {
        var reload = e.key === 'F5' || ((e.ctrlKey || e.metaKey) && !e.altKey && String(e.key).toLowerCase() === 'r');
        if (!reload || e.defaultPrevented || !dirtyForms().length) return;
        e.preventDefault();
        confirmDiscard(function () { location.reload(); });
    }, true);

    // ---- Browser Back button -------------------------------------------------------
    // Once the page has unsaved changes we park one extra history entry on it, so Back
    // lands on this same page first and we can ask before really leaving.
    var parked = false;
    function park() {
        if (parked || !dirtyForms().length) return;
        parked = true;
        history.pushState({ jkUnsavedGuard: true }, '');
    }
    ['input', 'change'].forEach(function (type) {
        document.addEventListener(type, function (e) { if (e.isTrusted) park(); }, true);
    });
    window.addEventListener('popstate', function (e) {
        if (!parked) return;
        if (!dirtyForms().length) { parked = false; history.back(); e.stopImmediatePropagation(); return; }
        e.stopImmediatePropagation(); // keep htmx from restoring/swapping the page
        history.pushState({ jkUnsavedGuard: true }, ''); // stay on this page
        confirmDiscard(function () { parked = false; history.go(-2); });
    }, true);

    // ---- Close tab / browser Reload button / typed URL ---------------------------
    // Browsers only allow their own generic dialog here; this is the last safety net
    // for the exits the page cannot intercept.
    window.addEventListener('beforeunload', function (e) {
        if (!dirtyForms().length) return;
        e.preventDefault();
        e.returnValue = '';
    });

    window.jkUnsaved = {
        isDirty: function () { return dirtyForms().length > 0; },
        confirm: confirmDiscard,
        markClean: markClean,
    };
})();
