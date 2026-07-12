"""Split a monolithic pages/*.html — extract the main inline <script> to a sibling .js.

Mechanizes the frontend P4 Atomic split (.claude/rules/multi-module-apps.md,
"Frontend counterpart — splitting a monolithic pages/index.html"). The pattern
was applied by hand to portal, earthing, cable-network, kb, and radio before
this script existed; this codifies it so the next split is one command.
Backend sibling: scripts/decompose_app.py (splits a monolithic app.py).

What it does (behavior-preserving by construction):
  1. Finds the LARGEST inline <script> block (no src=) in the page.
  2. Moves its body verbatim into a sibling pages/<name>.js with a banner
     comment noting the extraction.
  3. Replaces the inline block with one tag at the SAME position:
         <script src="<web-prefix>/pages/<name>.js"></script>
     so global scope and load order vs eos.js / eos-components.js are
     unchanged (external script without defer/async executes in document
     order exactly like the inline block did).
  4. Optionally validates the extracted file with `node --check`.

Usage:
  python scripts/split_page_js.py apps/<track>/<app>/pages/index.html
  python scripts/split_page_js.py <page.html> --name custom --dry-run
  python scripts/split_page_js.py <page.html> --min-lines 200

The web prefix and default <name> come from the app's manifest.toml
([provides.web] prefix / [app] id). Refuses to overwrite an existing .js,
refuses tiny blocks (default --min-lines 100 — a small inline block is not
a monolith; see the rule's "don't pre-split" guidance), and leaves smaller
inline blocks (e.g. <script>EOS.nav('x');</script>) untouched.

Static files hot-reload — no daemon restart needed after a split.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# Opening tag must not carry src=; attributes (e.g. type="module") are preserved.
SCRIPT_RE = re.compile(
    r"(?P<indent>[ \t]*)<script(?P<attrs>(?:(?!\bsrc\s*=)[^>])*)>(?P<body>.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

BANNER = (
    "// {app_id} -- page logic, extracted verbatim from pages/{html_name} (P4 Atomic\n"
    "// split, .claude/rules/multi-module-apps.md frontend pattern). Loaded at the\n"
    "// same position as the old inline <script>, so global scope and load order\n"
    "// vs eos.js / eos-components.js are unchanged.\n"
)


def find_app_root(html_path: Path) -> Path:
    """Walk up from the page to the directory holding manifest.toml."""
    for parent in html_path.parents:
        if (parent / "manifest.toml").is_file():
            return parent
    raise SystemExit(f"error: no manifest.toml found above {html_path}")


def read_manifest(app_root: Path) -> tuple[str, str]:
    """Return (app_id, web_prefix) from the app's manifest."""
    data = tomllib.loads((app_root / "manifest.toml").read_text(encoding="utf-8"))
    app_id = data.get("app", {}).get("id", "")
    prefix = data.get("provides", {}).get("web", {}).get("prefix", "")
    if not app_id:
        raise SystemExit(f"error: no [app] id in {app_root / 'manifest.toml'}")
    if not prefix:
        raise SystemExit(
            f"error: no [provides.web] prefix in {app_root / 'manifest.toml'} "
            "- a page split needs the served URL prefix"
        )
    return app_id, prefix


def pick_main_block(html: str) -> re.Match | None:
    """Largest inline <script> block by line count, or None."""
    best: re.Match | None = None
    best_lines = 0
    for m in SCRIPT_RE.finditer(html):
        n = m.group("body").count("\n")
        if n > best_lines:
            best, best_lines = m, n
    return best


def node_check(js_path: Path) -> str:
    """Run `node --check`; return '' on pass, message on fail/unavailable."""
    node = shutil.which("node")
    if not node:
        return "node not on PATH - syntax check skipped"
    r = subprocess.run(
        [node, "--check", str(js_path)], capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        return r.stderr.strip() or "node --check failed"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("page", help="path to the pages/*.html to split")
    ap.add_argument("--name", help="basename for the extracted .js (default: app id)")
    ap.add_argument(
        "--min-lines", type=int, default=100,
        help="refuse to extract a block smaller than this (default 100)",
    )
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    html_path = Path(args.page).resolve()
    if not html_path.is_file():
        raise SystemExit(f"error: {html_path} not found")
    if html_path.parent.name != "pages":
        raise SystemExit(f"error: {html_path} is not under a pages/ directory")

    app_root = find_app_root(html_path)
    app_id, prefix = read_manifest(app_root)
    name = args.name or app_id

    js_path = html_path.parent / f"{name}.js"
    if js_path.exists():
        raise SystemExit(
            f"error: {js_path} already exists - pass --name to pick another basename"
        )

    # newline='' preserves the file's existing line endings on round-trip.
    with open(html_path, encoding="utf-8", newline="") as f:
        html = f.read()

    m = pick_main_block(html)
    if not m:
        raise SystemExit("error: no inline <script> block found")
    block_lines = m.group("body").count("\n")
    if block_lines < args.min_lines:
        raise SystemExit(
            f"error: largest inline block is only ~{block_lines} lines "
            f"(< --min-lines {args.min_lines}) - this page is not a monolith"
        )

    start_line = html[: m.start()].count("\n") + 1
    attrs = m.group("attrs").rstrip()
    src_tag = f'{m.group("indent")}<script{attrs} src="{prefix}/pages/{name}.js"></script>'

    body = m.group("body")
    # The body almost always starts with the newline after `<script>`; drop
    # exactly that one so the banner sits flush. Everything else is verbatim.
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    js_text = BANNER.format(app_id=app_id, html_name=html_path.name) + body
    if not js_text.endswith("\n"):
        js_text += "\n"

    new_html = html[: m.start()] + src_tag + html[m.end():]

    print(f"page:    {html_path}")
    print(f"block:   line {start_line}, ~{block_lines} lines of JS")
    print(f"extract: {js_path}")
    print(f"tag:     {src_tag.strip()}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    with open(js_path, "w", encoding="utf-8", newline="") as f:
        f.write(js_text)
    with open(html_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_html)

    err = node_check(js_path)
    if err:
        print(f"warning: {err}", file=sys.stderr)
        # A failed syntax check on a verbatim move means the page was already
        # broken or the regex grabbed the wrong span - surface loudly.
        if "skipped" not in err:
            return 1
    else:
        print("node --check: OK")

    html_lines = new_html.count("\n") + 1
    print(f"done: {html_path.name} now ~{html_lines} lines; "
          f"{js_path.name} ~{js_text.count('\n')} lines")
    print("note: stage the new .js together with the edited .html or the page breaks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
