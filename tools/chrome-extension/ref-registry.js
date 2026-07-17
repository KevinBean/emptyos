(() => {
  // Pure element-reference lifecycle for the Browser Session page bridge.
  //
  // Extracted from page-bridge.js so the two load-bearing rules are node-testable
  // (the reading-policy.js precedent: content-script logic that only a Chromium
  // walk could catch belongs in a requireable module). No DOM/chrome coupling —
  // element handles are opaque; the caller injects sigOf(el), isConnected(el),
  // and newVersion().
  //
  // The two rules, and the bug that motivated the split (2026-07-15):
  //   * A ref must SURVIVE unrelated DOM churn. The earlier bridge observed the
  //     whole document (childList+subtree+ATTRIBUTES) and wiped every ref on any
  //     mutation, so on a page with a live 3D viewport / clock / spinner a ref
  //     died the instant it was issued and click/fill/select could never land.
  //   * A ref must be REFUSED when its own element is removed or repurposed — the
  //     "page swapped a different control under you" case. Enforced per-element at
  //     lookup() time by identity signature (role + accessible name), NOT by a
  //     document-wide observer.
  function createRefRegistry({ sigOf, isConnected, newVersion }) {
    let version = newVersion();
    let refs = new Map(); // ref -> { el, sig }
    let serial = 0; // monotonic across snapshots so a stale ref can never
    //                collide with a new element's ref.

    return {
      version: () => version,
      // Begin a fresh snapshot generation. Refs from the prior generation no
      // longer resolve; serial keeps counting up.
      reset() {
        version = newVersion();
        refs = new Map();
      },
      // Register an element for the current generation, returning its ref
      // (deduped within the generation).
      add(el) {
        for (const [ref, entry] of refs) if (entry.el === el) return ref;
        const ref = "e" + (++serial);
        refs.set(ref, { el, sig: sigOf(el) });
        return ref;
      },
      // Resolve a ref to its element, or throw "stale_ref". An explicit
      // wantVersion from an older generation cannot resolve; otherwise the
      // element must still be connected AND carry its captured identity.
      lookup(ref, wantVersion) {
        if (wantVersion && wantVersion !== version) throw new Error("stale_ref");
        const entry = refs.get(String(ref || ""));
        if (!entry || !isConnected(entry.el)) throw new Error("stale_ref");
        if (sigOf(entry.el) !== entry.sig) throw new Error("stale_ref");
        return entry.el;
      },
    };
  }

  globalThis.EOS_REF_REGISTRY = { createRefRegistry };
  // Requireable from node — this is how the two rules above are actually tested.
  if (typeof module !== "undefined" && module.exports) module.exports = { createRefRegistry };
})();
