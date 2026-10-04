// eos-workspace.js — the shared mount/unmount lifecycle for CAD-shell workspaces.
//
// The /cad/ shell loads a workspace module and calls its mount(ctx)/unmount().
// Every workspace module repeats the same lifecycle shell: inject a <style>,
// render its markup into ctx.root, run its own boot, and on teardown remove the
// style + clear the root. This helper owns that 80%; each module supplies only
// its boot (onMount) and its specific teardown (onUnmount — remove listeners etc).
//
// Extracted once three modules shared it (part / site-layout / topology),
// per CLAUDE.md rule 9 ("build specific first; extract on the second consumer").
//
// Usage:
//   import { defineWorkspace } from '/static/eos-workspace.js';
//   const _ws = defineWorkspace({
//     id: 'part', title: 'Part', styles: STYLES, markup: MARKUP,
//     onMount: async (ctx) => { ...wire + boot, ctx.root is already populated... },
//     onUnmount: () => { ...remove window listeners, null the viewport... },
//   });
//   export const mount = _ws.mount;
//   export const unmount = _ws.unmount;
//
// ctx = { root, workspace, params, api, setStatus, setTitle, navigate } (from the shell).

// Fetch helper bound to a path prefix, returning parsed JSON (or text). The
// shell binds it to a workspace's api_prefix (ctx.api); the base CAD binds it to
// an extension's api_prefix (cadApi.api), so a module never hardcodes its prefix.
export function makeApi(prefix) {
  return function api(path, opts) {
    return fetch((prefix || '') + path, opts || {}).then(r => {
      const ct = r.headers.get('content-type') || '';
      return ct.includes('application/json') ? r.json() : r.text();
    });
  };
}

// `defineWorkspace` (the shared CAD-shell mount/unmount lifecycle) was RETIRED once
// every workspace migrated off the legacy `?workspace=` shell: topology / part /
// site-layout became layout-host LAYOUTS (.claude/rules/cad-layouts.md), and the 2D
// `draft` workspace — the last consumer — inlined the ~30-line lifecycle directly
// (apps/extension/engineering/cad/pages/draft-workspace.js). Only `makeApi` (above)
// remains here, still used by the cad shell + the cadApi extension surface.
