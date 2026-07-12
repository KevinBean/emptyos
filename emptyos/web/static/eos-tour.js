/* EmptyOS Product Tour orchestrator.
 *
 * Walks the user through real pages: each step navigates with location.href
 * (full page load — apps live at distinct prefixes) and uses
 * EOS_UI.spotlight() to highlight the target element. State persists in
 * localStorage so the tour resumes on the next page load.
 *
 * Usage:
 *   EOS.tour.start();        // begin from step 0 (or resume if active)
 *   EOS.tour.dismiss();      // mark dismissed server-side + clear local state
 *   EOS.tour.maybeMount();   // auto-mount: if a tour is active, show current step
 *
 * The /tour/api/steps endpoint returns the capability-filtered step list;
 * steps that need a missing capability are auto-rewritten to /system?capability=…
 * (see apps/tour/app.py).
 */
(function() {
    var STORAGE_KEY = 'eos.tour.v1';
    var POLL_MAX_MS = 6000;

    function loadLocal() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); }
        catch (e) { return null; }
    }
    function saveLocal(state) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
    }
    function clearLocal() {
        try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    }

    async function fetchSteps() {
        var r = await fetch('/tour/api/steps');
        if (!r.ok) throw new Error('tour/api/steps ' + r.status);
        var data = await r.json();
        return data.steps || [];
    }

    function pathFromRoute(route) {
        // route may include a query string. Compare pathname only.
        try { return new URL(route, location.origin).pathname; }
        catch (e) { return route.split('?')[0]; }
    }

    async function showStep(steps, index) {
        if (index < 0 || index >= steps.length) {
            return finish();
        }
        var step = steps[index];
        var stepPath = pathFromRoute(step.route);
        // If we're not on the right page, navigate there. The next page load
        // will resume on this step (loadLocal returns {index, ...}).
        if (location.pathname !== stepPath) {
            saveLocal({active: true, index: index});
            location.href = step.route;
            return;
        }
        saveLocal({active: true, index: index});
        // Tell the server (best-effort) for cross-device resume.
        fetch('/tour/api/state', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({last_step: step.id}),
        }).catch(function() {});

        var label = (index + 1) + ' / ' + steps.length;
        var spot = EOS_UI.spotlight(step.spotlight, {
            stepLabel: label,
            title: step.title,
            body: step.body,
            onSkip: function() { dismiss(spot, false); },
            onPrev: index > 0 ? function() { spot.close(); showStep(steps, index - 1); } : null,
            onNext: function() { spot.close(); showStep(steps, index + 1); },
            nextLabel: index === steps.length - 1 ? 'Finish' : 'Next →',
        });
    }

    async function start() {
        var steps;
        try { steps = await fetchSteps(); }
        catch (e) { console.warn('[tour] could not load steps:', e); return; }
        if (!steps.length) return;
        showStep(steps, 0);
    }

    async function dismiss(spot, completed) {
        clearLocal();
        if (spot && spot.close) spot.close();
        try {
            await fetch('/tour/api/dismiss', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({completed: !!completed}),
            });
        } catch (e) {}
    }

    function finish() {
        // Last step done — clear local + mark completed server-side.
        dismiss(null, true);
    }

    async function maybeMount() {
        var local = loadLocal();
        if (!local || !local.active) return;
        var steps;
        try { steps = await fetchSteps(); }
        catch (e) { return; }
        if (!steps.length) { clearLocal(); return; }
        // Clamp index in case steps changed between sessions.
        var idx = Math.max(0, Math.min(local.index || 0, steps.length - 1));
        showStep(steps, idx);
    }

    // Public API
    window.EOS = window.EOS || {};
    window.EOS.tour = {
        start: start,
        dismiss: function() { return dismiss(null, false); },
        maybeMount: maybeMount,
        isActive: function() { var s = loadLocal(); return !!(s && s.active); },
    };

    // Auto-resume: if a tour is in progress, show the current step on every
    // page load. Apps don't need to opt in — the orchestrator just needs to
    // be loaded (we add it to the home page + /system; other apps optional).
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', maybeMount);
    } else {
        maybeMount();
    }
})();
