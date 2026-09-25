/* Proactive — pure grouping of the mute catalog into display sections.
 *
 * Extracted from index.html's renderKinds so it is reachable by tests/js
 * (.claude/rules/testing.md — the node --test shim loads a shipped
 * browser-global .js; it cannot load a page's inline boot IIFE). What it
 * guards is the one thing that must never break: the catalog IS the mute UI,
 * so a kind that fails to RENDER is a notification with no off switch.
 *
 * Namespaced per .claude/rules/multi-module-apps.md § frontend counterpart —
 * this file and index.html share one global scope, and the later-parsed
 * declaration silently wins, so nothing here is a bare top-level name.
 */
window.PROACTIVE_GROUPS = (function () {
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };

  /* group(catalog, groups) -> [{name, members}]
   *
   * Every catalog key comes back exactly once, in group order, with anything
   * unplaced in a trailing "Other" section. Ghost members (named by a group but
   * absent from the catalog) are dropped; a kind named in two groups appears
   * only in the first — two checkboxes for one kind desync after a click.
   *
   * hasOwnProperty throughout, and a null-prototype `placed`: a kind named
   * `constructor` / `toString` / `__proto__` is returned by Object.keys but
   * inherits a truthy value through the prototype chain, so a bare `k in cat`
   * or `!placed[k]` would drop its toggle entirely — the exact failure this
   * function exists to make impossible.
   */
  function group(catalog, groups) {
    var cat = catalog || {};
    var placed = Object.create(null);
    var out = [];
    (groups || []).forEach(function (g) {
      var name = g && g[0];
      var members = ((g && g[1]) || []).filter(function (k) {
        if (!has(cat, k) || placed[k]) return false;
        placed[k] = true;
        return true;
      });
      if (members.length) out.push({ name: name, members: members });
    });
    var leftover = Object.keys(cat).filter(function (k) { return !placed[k]; });
    if (leftover.length) out.push({ name: 'Other', members: leftover });
    return out;
  }

  return { group: group };
})();
