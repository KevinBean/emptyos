#!/usr/bin/env python3
"""scripts/build_design_card.py — turn an EmptyOS app page into a self-contained
Claude Design card.

An app page links `/static/theme.css` + `/static/eos-components.css` and renders
its content via JS from live data — none of which the Claude Design canvas can
load or run. This builds a faithful **themed shell**: inline the real stylesheets
so tokens resolve on the canvas, strip behaviour (a static design card), pin the
`theme-eos` class, and stamp the `@dsCard` marker the canvas indexes by.

Proven on `reader` (2026-06-22); generalised here so the conversation-mode push
(me → DesignSync) and the daemon `claude-design` service (`push`) share one
card-builder. Output: `.design-sync/apps/<id>.html` (git-excluded), ready to push.

Honest limit: the card shows themed layout + components, NOT live content (the
data is JS-driven). Good for reviewing chrome/theme; seed demo content by hand
only for an app you actually intend to redesign.

Usage:
  python scripts/build_design_card.py reader                 # by app dir name
  python scripts/build_design_card.py apps/.../pages/foo.html # by page path
  python scripts/build_design_card.py --all-fixed            # every page the scanner calls green
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from check_base import REPO_ROOT

STATIC = REPO_ROOT / "emptyos" / "web" / "static"
OUT_DIR = REPO_ROOT / ".design-sync" / "apps"
_LINK_RE = re.compile(r'<link[^>]+/static/(?:theme|eos-components|eos-keys)\.css[^>]*>', re.I)
_SCRIPT_RE = re.compile(r'<script\b[^>]*>.*?</script>', re.S | re.I)
_SELFSCRIPT_RE = re.compile(r'<script\b[^>]*/>', re.I)


def find_page(target: str) -> Path:
    """Resolve an app dir name OR a page path to a concrete pages/*.html file."""
    p = (REPO_ROOT / target) if not Path(target).is_absolute() else Path(target)
    if p.suffix == ".html" and p.exists():
        return p
    hits = sorted((REPO_ROOT / "apps").glob(f"**/{target}/pages/index.html"))
    if not hits:
        raise SystemExit(f"no page found for {target!r} (tried path + apps/**/{target}/pages/index.html)")
    return hits[0]


def app_id(page: Path) -> str:
    parts = page.parts
    return parts[parts.index("pages") - 1] if "pages" in parts else page.stem


def build_card(page: Path) -> Path:
    page_html = page.read_text(encoding="utf-8")
    theme = (STATIC / "theme.css").read_text(encoding="utf-8")
    comps = (STATIC / "eos-components.css").read_text(encoding="utf-8")

    # theme class on <html> so tokens resolve standalone on the canvas
    out = re.sub(r'<html\b[^>]*>', '<html lang="en" class="theme-eos">', page_html, count=1)
    out = _LINK_RE.sub("", out)                       # drop /static css links
    out = _SCRIPT_RE.sub("", out)                     # strip behaviour (static card)
    out = _SELFSCRIPT_RE.sub("", out)
    inline = f"<style>\n{theme}\n</style>\n<style>\n{comps}\n</style>\n"
    out = out.replace("</head>", inline + "</head>", 1)
    out = '<!-- @dsCard group="Apps" -->\n' + out

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{app_id(page)}.html"
    dest.write_text(out, encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a self-contained Claude Design card from an app page.")
    ap.add_argument("target", nargs="?", help="app dir name or page path")
    ap.add_argument("--all-fixed", action="store_true",
                    help="build a card for every page the consistency scanner reports green")
    args = ap.parse_args()

    pages: list[Path] = []
    if args.all_fixed:
        import json, subprocess
        r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "check_ui_consistency.py"), "--json"],
                           cwd=REPO_ROOT, capture_output=True, text=True)
        data = json.loads(r.stdout)["data"]["pages"]
        pages = [REPO_ROOT / p["path"] for p in data if p["severity"] == "green"]
    elif args.target:
        pages = [find_page(args.target)]
    else:
        ap.error("give an app/page target or --all-fixed")

    for pg in pages:
        dest = build_card(pg)
        print(f"  ✓ {app_id(pg):24} → {dest.relative_to(REPO_ROOT)}  ({dest.stat().st_size:,} B)")
    print(f"{len(pages)} card(s) built in {OUT_DIR.relative_to(REPO_ROOT)} — push via DesignSync / the claude-design service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
