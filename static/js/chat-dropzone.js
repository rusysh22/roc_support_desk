/*
 * Drag & drop file attachments for chat panels (staff ticket chat and the
 * portal "My Ticket" chat).
 *
 *   initChatDropzone(zoneEl, onFiles, { enabled: () => bool, hint: '...' })
 *
 * Shows a "Drop files to attach" overlay while files are dragged over
 * zoneEl, then hands the dropped FileList to onFiles — which should feed the
 * panel's existing attachment queue so size/count limits and previews stay
 * in one place. Text/link drags are ignored (only drags carrying Files).
 */
(function () {
    function hasFiles(e) {
        const types = e.dataTransfer && e.dataTransfer.types;
        return !!types && Array.prototype.indexOf.call(types, 'Files') !== -1;
    }

    window.initChatDropzone = function (zone, onFiles, opts) {
        if (!zone || zone.__chatDropzone) return;
        zone.__chatDropzone = true;
        opts = opts || {};
        const enabled = opts.enabled || function () { return true; };

        if (getComputedStyle(zone).position === 'static') zone.style.position = 'relative';
        const overlay = document.createElement('div');
        overlay.className = 'jk-chat-dropzone';
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML =
            '<div class="jk-chat-dropzone__inner">' +
            '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>' +
            '<span class="jk-chat-dropzone__title">Drop files to attach</span>' +
            (opts.hint ? '<span class="jk-chat-dropzone__hint"></span>' : '') +
            '</div>';
        if (opts.hint) overlay.querySelector('.jk-chat-dropzone__hint').textContent = opts.hint;
        zone.appendChild(overlay);

        // dragenter/dragleave fire for every child element crossed, so track
        // depth instead of toggling on each event.
        let depth = 0;
        const show = () => overlay.classList.add('is-active');
        const hide = () => { depth = 0; overlay.classList.remove('is-active'); };

        zone.addEventListener('dragenter', (e) => {
            if (!hasFiles(e) || !enabled()) return;
            e.preventDefault();
            depth++;
            show();
        });
        zone.addEventListener('dragover', (e) => {
            if (!hasFiles(e) || !enabled()) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
        });
        zone.addEventListener('dragleave', (e) => {
            if (!hasFiles(e)) return;
            depth = Math.max(0, depth - 1);
            if (depth === 0) hide();
        });
        zone.addEventListener('drop', (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            hide();
            if (!enabled()) return;
            const files = e.dataTransfer.files;
            if (files && files.length) onFiles(files);
        });
        // Drag cancelled (Esc) or dropped outside the window.
        window.addEventListener('dragend', hide);
        window.addEventListener('drop', hide);
    };
})();
