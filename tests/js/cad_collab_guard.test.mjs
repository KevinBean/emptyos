// Node behavior tests for eos-cad-collab-guard.js — the same-origin check that
// stands between a CAD invite link and connecting to an attacker-controlled
// WebSocket (see the crafted-live_ws finding this module was extracted to fix).
// Run: `node tests/js/cad_collab_guard.test.mjs`, or via
// tests/test_unit_cad_collab_guard.py.

import { isSameOriginWsUrl } from '../../emptyos/web/static/eos-cad-collab-guard.js';

let ok = 0, fail = 0;
const eq = (a, b, m) => {
  if (a === b) ok++;
  else { fail++; console.log('FAIL', m, '\n  got ', a, '\n  want', b); }
};

const HERE = 'https://cad.example/cad/?view=part-edit&id=doc-1';

// ── legitimate same-origin invite links, both ws schemes ──
eq(isSameOriginWsUrl('wss://cad.example/api/documents/doc-1/live/sync', HERE), true,
  'same-host wss: URL is accepted');
eq(isSameOriginWsUrl('ws://cad.example/api/documents/doc-1/live/sync', HERE), true,
  'same-host ws: URL is accepted (e.g. local http dev)');
eq(isSameOriginWsUrl('wss://cad.example:443/api/documents/doc-1/live/sync', HERE), true,
  'an explicit default port still matches (URL normalises it)');

// ── the actual attack this guards against ──
eq(isSameOriginWsUrl('wss://evil.example/collect', HERE), false,
  'a cross-origin host is refused — the exfiltration case');
eq(isSameOriginWsUrl('wss://cad.example.evil.example/collect', HERE), false,
  'a lookalike host that merely CONTAINS the real host is refused');
eq(isSameOriginWsUrl('wss://cad.example:9999/collect', HERE), false,
  'a different port on the same hostname is a different host and is refused');

// ── wrong scheme, even if the host matches ──
eq(isSameOriginWsUrl('https://cad.example/api/documents/doc-1/live/sync', HERE), false,
  'http(s): is not a websocket scheme, even same-origin');
eq(isSameOriginWsUrl('javascript:alert(1)', HERE), false,
  'a non-URL-like scheme is refused, not just non-ws');

// ── malformed / absent input never throws ──
eq(isSameOriginWsUrl('', HERE), false, 'empty string is refused');
eq(isSameOriginWsUrl(null, HERE), false, 'null does not throw');
eq(isSameOriginWsUrl(undefined, HERE), false, 'undefined does not throw');
eq(isSameOriginWsUrl('not a url at all', HERE), false, 'unparseable string is refused, not thrown');
eq(isSameOriginWsUrl(42, HERE), false, 'a non-string value is refused, not thrown');

if (fail) { console.log(`\n${fail} FAILED, ${ok} passed`); process.exit(1); }
console.log(`ALL ${ok} PASS`);
