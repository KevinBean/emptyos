#!/usr/bin/env python3
"""scripts/check_ui_consistency.py — EOS_UI consistency scanner.

Static, read-only scan of every app page (`apps/**/pages/*.html`) for the five
consistency signals that the `promote` rebuild (2026-06-22) surfaced: pages that
re-implement components EOS_UI already provides, or carry the broken theme
bootstrap that defaults to a non-existent theme and forces hardcoded hex
fallbacks. The graduated form of the one-off grep, per .claude/rules/audits.md +
self-audit-loops.md. Reference-clean page: apps/extension/dev/promote/pages/index.html.

Five signals (per page):

  S1  broken theme bootstrap  — an inline `eos-theme` bootstrap that does NOT
      carry the canonical 6-theme validation list (it can't self-heal an unknown
      theme, so it renders the page with undefined tokens). 🔴 mis-render.
      Source of truth for the correct snippet: emptyos/web/server.py
      ::_inject_theme_bootstrap (default 'eos', validate, self-heal).
  S2  hardcoded hex fallback  — `var(--x, #hex)`; defeats theming. 🟠 if heavy.
  S3  bespoke `.btn`          — re-implements `.eos-btn`. 🟡
  S4  bespoke `.badge`        — re-implements `.eos-badge`. 🟡
  S5  `system-ui` font        — instead of `var(--font)`. 🟡

Severity per page: 🔴 S1 · 🟠 (no S1) hex≥HEX_HEAVY · 🟡 any other signal · 🟢 clean.

Static signals are a FLOOR not a ceiling — a 🟢 page can still have layout/spacing
drift or a missing EOS_UI.* factory, so the sweep still gives every page a
judgment pass (the scanner ranks the order, it does not shrink the list).

Allowlisted apps (intentionally-bespoke canvases) are scanned but excluded from
severity counts + the exit code — same shape as DESKTOP_ONLY in
tests/test_sys_mobile.py. Excludes `*.legacy.html` (comparison backups).

Exit code = number of 🔴 pages (exit-code-as-signal). Registered gate=False in
preflight (advisory) until the baseline is clean (.claude/rules/audits.md).

Pure file I/O — does NOT import emptyos.kernel (safe while the daemon is up,
.claude/rules/daemon-handling.md).

Usage::

    python scripts/check_ui_consistency.py            # ranked human table
    python scripts/check_ui_consistency.py --json     # agent-cli envelope
    python scripts/check_ui_consistency.py --app reader   # one app
    python scripts/check_ui_consistency.py --severity red # only 🔴 rows
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from check_base import REPO_ROOT

# Intentionally-bespoke canvases — scanned but not counted/gated. Keyed by the
# app directory name (the parent of pages/). Add an entry only for a genuine
# free-canvas/standalone surface, never to silence a fixable page.
ALLOWLIST: set[str] = {
    "viz",            # Three.js / canvas artifact generator
    "designer",       # AI web-page generator (its output IS bespoke HTML)
    "ppt",            # slide-deck shell
    "cable_network",  # georeferenced cable-route designer canvas
}

SKIP_DIR_PARTS = {"__pycache__", "_archive", "_retired"}
HEX_HEAVY = 5  # hex-fallback count at/above which a page (no S1) is 🟠 not 🟡

# ── Signal patterns ──────────────────────────────────────────────────────────
# S1 (theme bootstrap) has three states, because a page that mentions `eos-theme`
# blocks the server's auto-injection (server.py only injects when `eos-theme` is
# absent) and must therefore self-heal on its own:
#   broken  — applies a theme class but defaults to a non-canonical theme
#             (||'dark' / 'default' / 'light'), or has no boot default at all
#             (a setter-only page like settings): mis-renders on a clean load.
#   fragile — has a real default (||'eos') but no validation list, so it can't
#             self-heal a stale stored theme: renders today, breaks on bad state.
#   ok      — carries the canonical 6-theme validation list (self-heals), or has
#             no inline bootstrap at all (server injects the canonical one).
CANONICAL_THEMES = {"eos", "digital-garden", "soft-light", "warm-dark", "void-dark", "nord"}
RE_HAS_BOOTSTRAP = re.compile(r"eos-theme")
RE_CANONICAL_LIST = re.compile(r"digital-garden")   # half the self-heal-array signature
RE_CANONICAL_LIST2 = re.compile(r"void-dark")       # other half
# default theme captured from `getItem('eos-theme') || 'X'`
RE_BOOTSTRAP_DEFAULT = re.compile(r"eos-theme['\"]\)?\s*\|\|\s*['\"]([a-z-]+)['\"]")
# does the page apply a theme class at boot (`'theme-' + t`, className=, classList.add)?
RE_APPLIES_THEME = re.compile(r"'theme-'\s*\+|className\s*=\s*['\"]theme-|classList\.add\(\s*['\"]theme-")
# S2: var(--token, #hex)
RE_HEX_FALLBACK = re.compile(r"var\(\s*--[a-z0-9-]+\s*,\s*#[0-9a-fA-F]{3,8}")
# S3 / S4: a bespoke `.btn` / `.badge` *redefinition* — a page re-styling the
# shared theme.css `.btn` / `.badge` in its own <style>. We flag only the CSS
# SELECTOR that opens a rule (`.btn {`, `.btn.primary {`, `.btn:hover {`, `.btn,`),
# NOT `class="btn"` usage — using the shared theme.css class is correct, not
# bespoke. `[^-]` keeps `.eos-btn` / `.eos-badge` from matching.
RE_BESPOKE_BTN = re.compile(r'[^-]\.btn(?:\.[a-z-]+|::?[a-z-]+)?\s*[{,]')
RE_BESPOKE_BADGE = re.compile(r'[^-]\.badge(?:\.[a-z-]+|::?[a-z-]+)?\s*[{,]')
# S5: system-ui / -apple-system as the primary font (not via var(--font))
RE_SYSTEM_FONT = re.compile(r"font-family:\s*(system-ui|-apple-system)")

SEV_ORDER = {"red": 0, "orange": 1, "yellow": 2, "green": 3, "allowlisted": 4}
SEV_MARK = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢", "allowlisted": "▫"}

# Comments are stripped before component/hex matching so a prose mention of
# `.btn` / `.badge` in a CSS or HTML comment can't score as a bespoke component
# (the promote reference's own explanatory comment was the first false positive).
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def strip_comments(text: str) -> str:
    return RE_HTML_COMMENT.sub(" ", RE_BLOCK_COMMENT.sub(" ", text))


def iter_pages() -> list[Path]:
    """Every app page HTML, minus caches/archives and *.legacy.html backups."""
    root = REPO_ROOT / "apps"
    out: list[Path] = []
    for f in root.rglob("*.html"):
        if SKIP_DIR_PARTS & set(f.parts):
            continue
        if "pages" not in f.parts:
            continue
        if f.name.endswith(".legacy.html"):
            continue
        out.append(f)
    return sorted(out)


def app_dir_name(page: Path) -> str:
    """Directory name of the app owning this page (the parent of pages/)."""
    parts = page.parts
    try:
        i = parts.index("pages")
    except ValueError:
        return page.parent.name
    return parts[i - 1] if i > 0 else page.parent.name


def app_label(page: Path) -> str:
    """Path from apps/ down to the page, for display (e.g. public/standard/reader/index.html)."""
    rel = page.relative_to(REPO_ROOT / "apps")
    return str(rel.parent.parent / rel.name).replace("\\", "/") if "pages" in rel.parts else str(rel)


def scan_page(page: Path) -> dict:
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    # S1 (bootstrap) runs on raw text — its markers never live in comments.
    if not RE_HAS_BOOTSTRAP.search(text):
        bootstrap = "ok"  # no inline bootstrap → server injects the canonical one
    elif RE_CANONICAL_LIST.search(text) and RE_CANONICAL_LIST2.search(text):
        bootstrap = "ok"  # carries the self-heal validation list
    else:
        defaults = RE_BOOTSTRAP_DEFAULT.findall(text)
        bad = [d for d in defaults if d not in CANONICAL_THEMES]
        good = [d for d in defaults if d in CANONICAL_THEMES]
        if bad:
            bootstrap = "broken"  # defaults to a non-existent theme → mis-renders now
        elif good:
            bootstrap = "fragile"  # real default, no validation → breaks on stale state
        else:
            bootstrap = "broken"  # applies/blocks a theme but no safe boot default

    # Component/hex/font signals run on comment-stripped text.
    body = strip_comments(text)
    hex_n = len(RE_HEX_FALLBACK.findall(body))
    s3 = bool(RE_BESPOKE_BTN.search(body))
    s4 = bool(RE_BESPOKE_BADGE.search(body))
    s5 = bool(RE_SYSTEM_FONT.search(body))  # reported as a note, not a severity driver

    name = app_dir_name(page)
    allowlisted = name in ALLOWLIST
    if allowlisted:
        sev = "allowlisted"
    elif bootstrap == "broken":
        sev = "red"
    elif hex_n >= HEX_HEAVY:
        sev = "orange"
    elif bootstrap == "fragile" or hex_n:
        sev = "yellow"
    else:
        # S3/S4 (.btn/.badge restyle) + S5 (system-ui) are reported as NOTES, not
        # severity: theme.css ships `.btn`/`.badge` as shared classes, so a
        # token-pure restyle themes correctly across all 6 themes — it is not a
        # theming bug. Migrating those to `.eos-btn`/`.eos-badge` is an opt-in
        # cosmetic follow-up, never a blind mass-refactor (audits.md).
        sev = "green"

    return {
        "path": str(page.relative_to(REPO_ROOT)).replace("\\", "/"),
        "app": name,
        "label": app_label(page),
        "severity": sev,
        "signals": {
            "bootstrap": bootstrap,
            "hex_fallbacks": hex_n,
            "bespoke_btn": s3,
            "bespoke_badge": s4,
            "system_font": s5,
        },
    }


def _sig_str(sg: dict) -> str:
    bits = []
    if sg["bootstrap"] == "broken":
        bits.append("bootstrap🔴")
    elif sg["bootstrap"] == "fragile":
        bits.append("bootstrap-fragile")
    if sg["hex_fallbacks"]:
        bits.append(f"hex×{sg['hex_fallbacks']}")
    if sg["bespoke_btn"]:
        bits.append(".btn")
    if sg["bespoke_badge"]:
        bits.append(".badge")
    if sg["system_font"]:
        bits.append("system-ui")
    return ", ".join(bits) or "clean"


def main() -> int:
    ap = argparse.ArgumentParser(description="EOS_UI consistency scanner (5 static signals, severity-ranked).")
    ap.add_argument("--json", action="store_true", help="agent-cli JSON envelope")
    ap.add_argument("--app", help="only pages whose app dir name matches this")
    ap.add_argument("--severity", choices=["red", "orange", "yellow", "green"],
                    help="only rows at this severity")
    args = ap.parse_args()

    rows = [scan_page(p) for p in iter_pages()]
    if args.app:
        rows = [r for r in rows if r["app"] == args.app]
    rows.sort(key=lambda r: (SEV_ORDER[r["severity"]], r["label"]))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    red = counts.get("red", 0)
    need = red + counts.get("orange", 0) + counts.get("yellow", 0)
    summary = (
        f"{len(rows)} pages · {red}🔴 {counts.get('orange', 0)}🟠 "
        f"{counts.get('yellow', 0)}🟡 {counts.get('green', 0)}🟢 "
        f"{counts.get('allowlisted', 0)} allowlisted · {need} need work"
    )

    view = rows if not args.severity else [r for r in rows if r["severity"] == args.severity]

    if args.json:
        print(json.dumps({
            "ok": red == 0,
            "code": "ok" if red == 0 else "broken_bootstrap",
            "message": summary,
            "data": {"pages": view, "counts": counts, "hex_heavy_threshold": HEX_HEAVY},
        }))
        return red

    print(f"ui-consistency — {summary}\n")
    cur = None
    for r in view:
        if r["severity"] != cur:
            cur = r["severity"]
            print(f"{SEV_MARK[cur]} {cur}")
        print(f"    {r['label']:<46} {_sig_str(r['signals'])}")
    if red:
        print(f"\n{red} page(s) mis-render (broken theme bootstrap) — fix first: delete the bespoke")
        print("inline <script> so emptyos/web/server.py::_inject_theme_bootstrap injects the canonical one.")
    print(f"\n{summary}")
    return red


if __name__ == "__main__":
    sys.exit(main())
