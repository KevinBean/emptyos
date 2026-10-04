"""check-csp-inline.py — inline handlers on the export surface must stay
inside the eos-csp-bridge grammar.

Exported bundles can be packaged as MV3 Chrome extensions, whose CSP
(script-src 'self') blocks inline on*="" handlers. eos-csp-bridge.js
re-enables them via a small no-eval interpreter — but only for the grammar
it supports (calls, this/event args, return false, if/ternary, one-path
assignment). This scanner extracts every inline handler from the export
surface (static HTML attributes + handlers built inside JS innerHTML
strings) and parses each through the actual bridge, so an unsupported
handler fails here instead of dying silently inside the extension.

Usage:
    python scripts/check-csp-inline.py               # scan default surface
    python scripts/check-csp-inline.py <paths...>    # scan specific files
    python scripts/check-csp-inline.py --app <id>    # scan one app's export
                                                     # surface (pages/ minus
                                                     # vendor/, export.py) +
                                                     # the shared static files

Exit 0 = clean; exit 1 = incompatible handlers found (listed).
Requires node (skips with a warning when absent — advisory, per audits.md).
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "emptyos" / "web" / "static" / "eos-csp-bridge.js"

# Shared static files that ship inside every export bundle.
SHARED_STATIC = [
    ROOT / "emptyos" / "web" / "static" / "eos-components.js",
    ROOT / "emptyos" / "web" / "static" / "eos.js",
    ROOT / "emptyos" / "web" / "static" / "eos-export-shim.js",
]


def _app_surface(app_id: str) -> list[Path]:
    """One app's export surface: pages/ (minus vendored third-party JS,
    which carries no app handlers) + export.py (client_overrides JS)."""
    from emptyos.sdk.app_layout import iter_app_dirs

    for aid, app_dir in iter_app_dirs(ROOT / "apps", include_personal=True):
        if aid != app_id:
            continue
        out = []
        pages = app_dir / "pages"
        if pages.exists():
            out += [
                p for p in sorted(pages.rglob("*"))
                if p.suffix in (".html", ".js") and "vendor" not in p.parts
            ]
        if (app_dir / "export.py").exists():
            out.append(app_dir / "export.py")
        return out
    raise SystemExit(f"check-csp-inline: unknown app id {app_id!r}")



_ATTR_RE = re.compile(r'\bon(?:click|change|input|keydown|keyup|submit|blur|dblclick)\s*=\s*"([^"]*)"')
# Handlers built inside single-quoted JS strings interpolate values via
# '...' + expr + '...'. Reconstruct a representative final handler string:
#   pass B — join plain string-concat seams ('a' + \n 'b' → ab), incl. any
#   // comment between the seam's lines;
#   pass A — replace value interpolations (' + expr + ') with a placeholder
#   string literal '§' so the grammar check sees a parseable handler.
_CONCAT_SEAM_RE = re.compile(r"'\s*\+(?:\s|//[^\n]*)*'")
# Interpolation already wrapped in escaped quotes in the source
# (…,\''+item.file+'\',…) — placeholder must NOT add its own quotes.
_WRAPPED_INTERP_RE = re.compile(r"\\''\s*\+.*?\+\s*'\\'", re.S)
_INTERP_RE = re.compile(r"'\s*\+.*?\+\s*'", re.S)
# Prose in a `//` comment is not code. A comment documenting the helpers —
# `// Use jsArg only where YOU are writing the onclick="..." characters.` —
# otherwise reaches the bridge as a handler whose body is the literal `...`,
# which no parser accepts. That is a phantom finding, and it masked the real
# signal: the checker read 1 FAIL for a file with zero defective handlers.
# Only a comment that OPENS its line is stripped, so a `//` inside a handler
# string (a URL, a concat seam — see _CONCAT_SEAM_RE, which consumes comments
# *between* seams and must still see them) is untouched. The newline is kept
# so reported line positions do not shift.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//[^\n]*", re.M)


def _extract(path: Path) -> list[tuple[str, str]]:
    """Return (source_snippet, normalized_handler) pairs from one file.

    One code path for .html and .js/.py alike: HTML pages often build
    handlers inside inline <script> strings too, so they need the same
    interpolation normalization (static attributes pass through it as
    no-ops — they contain no '+'-concat seams or escaped quotes)."""
    text = path.read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []
    normalized = _LINE_COMMENT_RE.sub("", text)
    normalized = _CONCAT_SEAM_RE.sub("", normalized)
    normalized = _WRAPPED_INTERP_RE.sub(r"\\'ARG\\'", normalized)
    normalized = _INTERP_RE.sub("'ARG'", normalized)
    for m in _ATTR_RE.finditer(normalized):
        handler = m.group(1).replace("\\'", "'").replace('\\"', '"')
        handler = html.unescape(handler)
        if not handler.strip() or "\n" in handler:
            # A newline inside the captured value means the regex crossed a
            # string boundary the normalizer didn't understand — skip rather
            # than report a phantom (audits.md false-positive discipline).
            continue
        out.append((m.group(0)[:80], handler))
    return out


def main(argv: list[str]) -> int:
    if shutil.which("node") is None:
        print("check-csp-inline: node not available — skipping (advisory)")
        return 0
    if argv[:1] == ["--app"]:
        if len(argv) < 2:
            raise SystemExit("check-csp-inline: --app requires an app id")
        paths = _app_surface(argv[1]) + SHARED_STATIC
    elif argv:
        paths = [Path(p).resolve() for p in argv]
    else:
        paths = _app_surface("boards") + SHARED_STATIC
    handlers: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            label = str(p.relative_to(ROOT))
        except ValueError:
            label = str(p)
        for snippet, handler in _extract(p):
            handlers.append({"file": label, "snippet": snippet, "handler": handler})

    if not handlers:
        print("check-csp-inline: no inline handlers found")
        return 0

    # One node invocation parses everything through the real bridge.
    driver = (
        "const B = require(process.argv[2]);\n"
        "const items = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf-8'));\n"
        "const bad = [];\n"
        "for (const it of items) {\n"
        "  try { B.parse(it.handler); } catch (e) { bad.push({...it, error: e.message}); }\n"
        "}\n"
        "console.log(JSON.stringify(bad));\n"
    )
    with tempfile.TemporaryDirectory() as td:
        drv = Path(td) / "drv.js"
        payload = Path(td) / "handlers.json"
        drv.write_text(driver, encoding="utf-8")
        payload.write_text(json.dumps(handlers), encoding="utf-8")
        r = subprocess.run(
            ["node", str(drv), str(BRIDGE), str(payload)],
            capture_output=True, text=True, timeout=60,
        )
    if r.returncode != 0:
        print("check-csp-inline: node driver failed:\n" + r.stderr)
        return 1
    bad = json.loads(r.stdout or "[]")
    if not bad:
        print(f"check-csp-inline: OK — {len(handlers)} inline handlers all bridge-compatible")
        return 0
    print(f"check-csp-inline: {len(bad)} handler(s) OUTSIDE the bridge grammar:")
    for b in bad:
        print(f"  {b['file']}: {b['handler'][:90]}")
        print(f"    → {b['error']}")
    print("Fix: simplify the handler or move it into a real script file.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
