/* worklog — CPEng competency chip rendering.
 *
 * A sibling of worklog.js rather than a block inside it, for one reason: the
 * page's boot IIFE has load-time side effects, so `tests/js/shim.mjs` cannot
 * evaluate worklog.js without a DOM. Lifting the pure helper into its own
 * shipped file is the pattern .claude/rules/testing.md prescribes — the browser
 * loads the same bytes the test does, so there is no parallel build to drift.
 *
 * Self-contained on purpose: it does NOT use worklog.js's `esc`, both because
 * load order would make that fragile and because a shared top-level name across
 * a page and its siblings is one namespace (.claude/rules/multi-module-apps.md
 * § frontend counterpart).
 *
 * It renders what the SERVER parsed. An earlier cut carried a JS copy of
 * COMPETENCY_TAG_RE plus a hardcoded FOCUS_ELEMENTS = [11, 13]; both were
 * deleted after review measured them disagreeing with the server on
 * out-of-range tags and on ordering against attachment stripping — an item
 * could be counted as evidence by the roll-up and render with no chip. And the
 * focus map is generated from the vault by scripts/gen_competency_focus.py, so
 * a JS constant would keep an element red long after it had been evidenced.
 */
var renderCompetencyTags = (function () {
    function e(s) {
        return (s == null ? '' : String(s))
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    // elements: [11, 13] from /api/day. focus: {"11": "gap"} — string keys, as
    // JSON delivers them.
    return function (elements, focus) {
        focus = focus || {};
        return (elements || []).map(function (n) {
            n = parseInt(n, 10);
            if (!(n >= 1 && n <= 16)) return '';
            var kind = focus[String(n)] || '';
            // The WORD, not just the red — .claude/rules/list-card-density.md:
            // status is never conveyed by colour alone.
            return '<span class="cp-tag' + (kind ? ' focus' : '') + '"'
                + ' title="Engineers Australia Stage 2 element ' + n
                + (kind ? ' — ' + e(kind) : '') + '">'
                + '#c' + n + (kind ? ' ' + e(kind) : '') + '</span>';
        }).join('');
    };
})();
