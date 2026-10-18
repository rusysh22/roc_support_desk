/**
 * Running-text ("marquee") truncation for narrow badges/cells.
 *
 * Markup contract: a ".jk-marquee" wrapper (overflow:hidden, white-space:
 * nowrap) around a ".jk-marquee-inner" span holding the full text.
 *
 * Two variants:
 *  - Hover-triggered (default): on hover, the inner span slides left by
 *    exactly its own overflow so the truncated tail scrolls into view,
 *    then eases back on mouse-leave. Delegated on document (mouseover/
 *    mouseout bubble, unlike mouseenter/mouseleave) so it keeps working
 *    after htmx/Alpine swaps rows in without re-binding listeners.
 *  - Auto-running (".jk-marquee-auto" added alongside ".jk-marquee"): no
 *    hover needed — loops continuously via a CSS animation. Still only
 *    animates elements that actually overflow (re-measured on load,
 *    resize, and whenever matching nodes are added to the DOM, so it
 *    keeps working after htmx/Alpine swaps too).
 */
(function () {
    function speedFor(overflowPx) {
        // ~40px/sec, clamped so very long names don't take forever and
        // very short overflows don't flash by instantly.
        return Math.min(6, Math.max(0.6, overflowPx / 40));
    }

    // --- Hover-triggered variant ---
    document.addEventListener('mouseover', (e) => {
        const wrap = e.target.closest('.jk-marquee:not(.jk-marquee-auto)');
        if (!wrap || wrap.contains(e.relatedTarget)) return;
        const inner = wrap.querySelector('.jk-marquee-inner');
        if (!inner) return;
        const overflow = inner.scrollWidth - wrap.clientWidth;
        if (overflow <= 1) return;
        inner.style.transition = `transform ${speedFor(overflow)}s linear`;
        inner.style.transform = `translateX(-${overflow}px)`;
    });

    document.addEventListener('mouseout', (e) => {
        const wrap = e.target.closest('.jk-marquee:not(.jk-marquee-auto)');
        if (!wrap || wrap.contains(e.relatedTarget)) return;
        const inner = wrap.querySelector('.jk-marquee-inner');
        if (!inner) return;
        inner.style.transition = 'transform 0.3s ease';
        inner.style.transform = 'translateX(0)';
    });

    // --- Auto-running variant ---
    function initAuto(wrap) {
        const inner = wrap.querySelector('.jk-marquee-inner');
        if (!inner) return;
        const overflow = inner.scrollWidth - wrap.clientWidth;
        if (overflow <= 1) {
            wrap.classList.remove('jk-marquee-animate');
            inner.style.removeProperty('--jk-marquee-shift');
            inner.style.removeProperty('--jk-marquee-duration');
            return;
        }
        // Total loop includes pauses at both ends, so scale duration by
        // the scroll distance (2x, there and back) plus a flat pause budget.
        const duration = speedFor(overflow) * 2 + 1.5;
        inner.style.setProperty('--jk-marquee-shift', `-${overflow}px`);
        inner.style.setProperty('--jk-marquee-duration', `${duration}s`);
        wrap.classList.add('jk-marquee-animate');
    }

    function initAllAuto(root) {
        (root || document).querySelectorAll('.jk-marquee-auto').forEach(initAuto);
    }

    let resizeTimer = null;
    function scheduleReinit() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => initAllAuto(document), 150);
    }

    document.addEventListener('DOMContentLoaded', () => initAllAuto(document));
    window.addEventListener('resize', scheduleReinit, { passive: true });

    // Re-measure whenever new marquee nodes show up (htmx swaps, Alpine
    // re-renders, pagination, filtering) — cheaper than re-scanning the
    // whole document on every mutation.
    const observer = new MutationObserver((mutations) => {
        let found = false;
        for (const m of mutations) {
            for (const node of m.addedNodes) {
                if (node.nodeType !== 1) continue;
                if (node.matches && node.matches('.jk-marquee-auto')) {
                    initAuto(node);
                    found = true;
                } else if (node.querySelectorAll) {
                    const nested = node.querySelectorAll('.jk-marquee-auto');
                    if (nested.length) {
                        nested.forEach(initAuto);
                        found = true;
                    }
                }
            }
        }
        // Mutation targets rarely resize existing marquees in place, but
        // htmx can swap a row's innerHTML — a flag keeps this a no-op
        // when nothing relevant changed.
        if (found) { /* already initialized inline above */ }
    });

    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
    } else {
        document.addEventListener('DOMContentLoaded', () => {
            observer.observe(document.body, { childList: true, subtree: true });
        });
    }
})();
