// eos-cad-collab-guard.js — pure validation for a shared live-collaboration
// invite link (no DOM, no imports), kept import-free so it unit-tests under
// node directly (the caller, part-tools.js, is not node-importable — it pulls
// in /static/... absolute imports for the view shell).
//
// A CAD invite link carries `live_ws` in the query string; the server only
// ever mints a same-origin ws(s):// URL for it, but the browser has no way to
// tell a legitimate link from a crafted one just by looking at the query
// string. isSameOriginWsUrl is the one thing standing between "join the
// document's real live session" and "silently stream document edits to an
// attacker's WebSocket" — see .claude/rules/cad-extensions.md for the wider
// cadApi contract this collaboration feature plugs into.

export function isSameOriginWsUrl(rawUrl, currentHref) {
  if (!rawUrl || typeof rawUrl !== 'string') return false;
  let parsed;
  try {
    parsed = new URL(rawUrl, currentHref);
  } catch (e) {
    return false;
  }
  if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') return false;
  let current;
  try {
    current = new URL(currentHref);
  } catch (e) {
    return false;
  }
  return parsed.host === current.host;
}
