/* Auto-provenance: decorate declared AI-output sinks from JSON responses. */
(function _eosInitAutoProvenance() {
    'use strict';
    if (typeof window === 'undefined' || window._eosProvWrapped) return;
    window._eosProvWrapped = true;

    var MAX_BYTES = 512 * 1024;
    var CACHE_TTL = 60 * 1000;
    var CACHE_SIZE = 8;
    var cache = [];
    var origFetch = window.fetch.bind(window);
    var lastT0 = 0;

    function nextT0() {
        lastT0 = Math.max(Date.now(), lastT0 + 1);
        return lastT0;
    }

    function requestUrl(input) {
        try {
            var raw = typeof input === 'string' ? input : input && input.url;
            return raw ? new URL(raw, window.location.href) : null;
        } catch (_) { return null; }
    }

    function matchingSinks(path) {
        var all = document.querySelectorAll('[data-ai-output]');
        var matches = [];
        var best = -1;
        for (var i = 0; i < all.length; i++) {
            var prefix = all[i].getAttribute('data-ai-output') || '';
            if (prefix && path.indexOf(prefix) !== 0) continue;
            var specificity = prefix.length;
            if (specificity > best) {
                matches = [all[i]];
                best = specificity;
            } else if (specificity === best) {
                matches.push(all[i]);
            }
        }
        return matches;
    }

    function adjacentChip(sink) {
        var next = sink && sink.nextElementSibling;
        return next && next.classList.contains('eos-auto-provenance') ? next : null;
    }

    function titleFor(prov) {
        var bits = [];
        if (prov.latency_ms != null) bits.push(String(prov.latency_ms) + ' ms');
        if (Array.isArray(prov.citations) && prov.citations.length) {
            bits.push(prov.citations.length + (prov.citations.length === 1 ? ' citation' : ' citations'));
        }
        if (prov.under_powered) bits.push('Under-powered model');
        return bits.join(' · ');
    }

    function gcOrphans() {
        var chips = document.querySelectorAll('.eos-auto-provenance');
        for (var i = 0; i < chips.length; i++) {
            var sink = chips[i].previousElementSibling;
            if (!sink || !sink.matches('[data-ai-output]')) chips[i].remove();
        }
    }

    // Only the endpoint that painted a chip may clear it. A `data-ai-output=""`
    // sink matches every /api/ path, so without this the next unrelated chrome
    // call (GET /api/health, a poll) would erase a perfectly good attribution.
    function clearSink(sink, path, t0) {
        if (sink.getAttribute('data-prov-path') !== path) return;
        var previous = Number(sink.getAttribute('data-prov-t0') || 0);
        if (previous > t0) return;
        sink.setAttribute('data-prov-t0', String(t0));
        sink.removeAttribute('data-prov-path');
        var chip = adjacentChip(sink);
        if (chip) chip.remove();
    }

    function paintSink(sink, prov, t0, path) {
        if (!window.EOS_UI || typeof window.EOS_UI.provenance !== 'function') return;
        var previous = Number(sink.getAttribute('data-prov-t0') || 0);
        if (previous >= t0) return;
        var html = window.EOS_UI.provenance({
            mode: prov.mode,
            provider: prov.provider,
            model: prov.model,
            title: titleFor(prov),
        });
        if (!html) return;
        var chip = adjacentChip(sink);
        if (!chip) {
            chip = document.createElement('div');
            chip.className = 'eos-auto-provenance';
            sink.insertAdjacentElement('afterend', chip);
        }
        chip.setAttribute('data-prov-t0', String(t0));
        chip.innerHTML = html;
        sink.setAttribute('data-prov-t0', String(t0));
        sink.setAttribute('data-prov-path', path);
    }

    function applyResponse(path, prov, t0, remember) {
        var sinks = matchingSinks(path);
        if (!sinks.length) return;
        gcOrphans();
        if (!prov || !prov.mode) {
            // Drop only this endpoint's cached attribution, so the observer
            // cannot replay a chip the endpoint has just retracted.
            cache = cache.filter(function(entry) { return entry.path !== path; });
            for (var i = 0; i < sinks.length; i++) clearSink(sinks[i], path, t0);
            return;
        }
        if (remember) {
            cache.push({path: path, provenance: prov, t0: t0, expires: Date.now() + CACHE_TTL});
            if (cache.length > CACHE_SIZE) cache.splice(0, cache.length - CACHE_SIZE);
        }
        for (var j = 0; j < sinks.length; j++) paintSink(sinks[j], prov, t0, path);
    }

    function replayCache() {
        var now = Date.now();
        cache = cache.filter(function(entry) { return entry.expires > now; });
        // Nothing to replay and no chip that could have been orphaned: skip the
        // document-wide sweep. The observer fires on every mutation, including
        // each token of a streaming chat.
        if (!cache.length && !document.querySelector('.eos-auto-provenance')) return;
        gcOrphans();
        for (var i = 0; i < cache.length; i++) {
            applyResponse(cache[i].path, cache[i].provenance, cache[i].t0, false);
        }
    }

    window.fetch = function(input, init) {
        var url = requestUrl(input);
        var t0 = nextT0();
        var pending = origFetch(input, init);
        pending.then(function(res) {
            if (!url || !res || !res.ok || url.origin !== window.location.origin ||
                    url.pathname.indexOf('/api/') === -1 ||
                    !document.querySelector('[data-ai-output]')) return;
            var type = (res.headers.get('content-type') || '').toLowerCase();
            if (type.indexOf('application/json') === -1) return;
            var length = Number(res.headers.get('content-length') || 0);
            if (length > MAX_BYTES) return;
            var probe = res.clone();
            // The .catch covers only a non-JSON body. Decorating errors are logged
            // rather than swallowed — a blanket catch here once hid a ReferenceError
            // that silently disabled chip retraction.
            probe.json().then(function(body) {
                try {
                    var prov = body && !Array.isArray(body) ? body.provenance : null;
                    applyResponse(url.pathname, prov, t0, true);
                } catch (err) {
                    if (window.console) window.console.error('eos-provenance:', err);
                }
            }, function() {});
        }, function() {});
        return pending;
    };

    function bootObserver() {
        if (!document.body || !window.MutationObserver) return;
        var queued = false;
        var observer = new MutationObserver(function() {
            if (queued) return;
            queued = true;
            Promise.resolve().then(function() {
                queued = false;
                replayCache();
            });
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['data-ai-output'],
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootObserver);
    } else {
        bootObserver();
    }
})();
