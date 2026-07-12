#!/usr/bin/env python3
"""Structural sibling-ness scan for app pages (graduated from /eos-design-system-audit Phase 1c).

Detects pages that hand-roll the shared EOS_UI component vocabulary (modal /
toast / stat-cards / buttons) nearly wholesale instead of using
`eos-components.{js,css}` — the "doesn't feel like a sibling" drift that the
signature-based DL checks can't see.

Threshold tuning (2026-06-13, per .claude/rules/audits.md): the raw Phase 1c
heuristics were noisy — S-1 adoption-ratio < 0.30 fired on 103/130 healthy
pages and S-2 inline-CSS > 60 lines on 42, because EmptyOS apps legitimately
carry substantive app-specific classes and CSS. Both are demoted to
*informational* distribution stats here. The finding signal is the composite
S-3 rule, verified quiet on the whole healthy tree:

    a page is a structural outlier iff >= 3 of the 4 component cells
    (modal, toast, stat-cards, buttons) are in use AND <= 1 of them
    resolves to EOS_UI.

Brand islands (own `:root` token namespace) are exempt — they are deliberate
visual islands, not drift (see the island table in
.claude/skills/eos-design-system-audit/SKILL.md).

Duplication signals (2026-07-10 frontend design audit, `--duplication`) are
*informational only* and never affect the exit code:

    S-4  page defines its own esc()/escAttr() despite the global helpers
         (99 pages at audit time — pure copy-paste, and an XSS-hygiene tail)
    S-5  page calls raw fetch() and never once uses EOS.api

S-5 is deliberately the *never-adopted* case, not "any raw fetch": streaming,
NDJSON, and blob paths legitimately bypass the JSON wrapper, so a raw-fetch
count alone fires on healthy pages (audits.md — tune against healthy pages
before trusting a signal). Both signals are worklists for migration-on-touch,
not a gate.

Usage:
    python scripts/check_ui_structure.py               # scan tracked app pages
    python scripts/check_ui_structure.py --stats       # also print S-1/S-2 distributions
    python scripts/check_ui_structure.py --duplication # list S-4/S-5 pages (informational)
    python scripts/check_ui_structure.py --selftest    # classifier regression check

Exit code = number of structural-outlier pages (0 = clean). Advisory in
preflight (`--scope ui`, gate=False).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from check_base import REPO_ROOT as ROOT, git_tracked

SKIP_FRAGMENTS = ("/_retired/", "/_example/", "/_catalog/")

# Known carve-outs that the mechanical island rule below can't see (no private
# :root namespace, but a deliberate non-theme palette / separate document).
ISLAND_SUFFIXES = (
    "publish/portfolio_template.html",
    "reader/pages/index.html",
    "code/pages/index.html",
    "cable_network/pages/map.html",
    "gesture/pages/demo.html",
    "canvas/pages/index.html",
    "fault-distribution/pages/study.html",
    "quickref/pages/index.html",
)

STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}")
PRIVATE_TOKEN_RE = re.compile(r"--([a-z]{1,4})-[\w-]+\s*:")
CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')


def is_island(path: Path, css: str) -> bool:
    rel = path.as_posix()
    if any(rel.endswith(suf) for suf in ISLAND_SUFFIXES):
        return True
    # Mechanical rule: a :root block declaring >=3 same-prefix private tokens.
    for m in ROOT_BLOCK_RE.finditer(css):
        prefixes = Counter(PRIVATE_TOKEN_RE.findall(m.group(1)))
        if any(c >= 3 for c in prefixes.values()):
            return True
    return False


def fingerprint(full: str, css: str) -> dict[str, str]:
    """Classify the 4 component cells as EOS_UI / hand / mixed / none."""
    fp: dict[str, str] = {}
    fp["modal"] = ("EOS_UI" if re.search(r"EOS_UI\.(modal|formModal|confirm)\(", full)
                   else ("hand" if re.search(r"\.modal(-bg|-handle)?\s*\{", css) else "none"))
    if fp["modal"] == "EOS_UI" and re.search(r"\.modal(-bg|-handle)\s*\{", css):
        fp["modal"] = "mixed"
    fp["toast"] = ("EOS_UI" if "EOS_UI.toast(" in full
                   else ("hand" if re.search(r"\.toast(-ok|-err)?\s*\{", css) else "none"))
    fp["stats"] = ("EOS_UI" if ("EOS_UI.statCards(" in full or "eos-hero-card" in full)
                   else ("hand" if re.search(r"\.(hero-card|stat-card|kpi-card|hero-val)\b", css) else "none"))
    eos_btn = full.count("eos-btn")
    other_btn = len(re.findall(r'class="[^"]*\bbtn-', full))
    if eos_btn + other_btn > 0:
        share = eos_btn / (eos_btn + other_btn)
        fp["btns"] = "EOS_UI" if share > 0.7 else ("hand" if share < 0.3 else "mixed")
    else:
        fp["btns"] = "none"
    return fp


def is_outlier(fp: dict[str, str]) -> bool:
    in_use = sum(1 for v in fp.values() if v != "none")
    eos = sum(1 for v in fp.values() if v == "EOS_UI")
    return in_use >= 3 and eos <= 1


# S-4: a page-local definition of esc/escAttr, which eos.js already exposes
# globally (and EOS_UI.esc mirrors). Matches `function esc(`, `const esc =`,
# `var escAttr = `, etc. — never a call site, never `EOS_UI.esc = ...`.
LOCAL_ESC_RE = re.compile(
    r"(?:^|[;{}\n])\s*(?:function\s+esc(?:Attr)?\s*\(|"
    r"(?:const|let|var)\s+esc(?:Attr)?\s*=)"
)
RAW_FETCH_RE = re.compile(r"(?<![\w.])fetch\s*\(")
EOS_API_RE = re.compile(r"EOS\.api\s*\(")


def duplication_signals(full: str) -> dict[str, bool]:
    """Informational copy-paste signals. Never gate on these — see module docstring."""
    return {
        "local_esc": bool(LOCAL_ESC_RE.search(full)),
        "no_api_wrapper": bool(RAW_FETCH_RE.search(full)) and not EOS_API_RE.search(full),
    }


def page_files(path: Path) -> tuple[str, str, str]:
    """Return (html_text, css_of_first_style_blocks, html+sibling_js)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    css = "\n".join(STYLE_BLOCK_RE.findall(text))
    js = ""
    for sib in path.parent.glob("*.js"):
        if sib.name.endswith(".min.js"):
            continue
        try:
            js += sib.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return text, css, text + "\n" + js


def _scan_pages() -> list[Path]:
    out = []
    for p in git_tracked():
        if not p.is_relative_to(ROOT):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if not (rel.startswith("apps/") and rel.endswith(".html")):
            continue
        parts = rel.split("/")
        if len(parts) < 4 or parts[-2] != "pages":
            continue
        if any(skip in "/" + rel for skip in SKIP_FRAGMENTS):
            continue
        if p.exists():
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stats", action="store_true",
                    help="print informational S-1 adoption / S-2 inline-CSS distributions")
    ap.add_argument("--duplication", action="store_true",
                    help="list informational S-4 (local esc) / S-5 (no EOS.api) pages")
    ap.add_argument("--selftest", action="store_true",
                    help="run the classifier against embedded synthetic pages")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    pages = _scan_pages()
    outliers: list[tuple[Path, dict]] = []
    ratios: list[float] = []
    css_lines: list[int] = []
    dup_esc: list[Path] = []
    dup_api: list[Path] = []
    islands = 0

    for page in pages:
        text, css, full = page_files(page)
        if is_island(page, css):
            islands += 1
            continue
        fp = fingerprint(full, css)
        if is_outlier(fp):
            outliers.append((page, fp))
        dup = duplication_signals(full)
        if dup["local_esc"]:
            dup_esc.append(page)
        if dup["no_api_wrapper"]:
            dup_api.append(page)
        if args.stats:
            if "eos-components" in text:
                toks = [t for m in CLASS_ATTR_RE.finditer(full) for t in m.group(1).split()]
                if len(toks) >= 20:
                    ratios.append(sum(1 for t in toks if t.startswith("eos-")) / len(toks))
            blocks = STYLE_BLOCK_RE.findall(text)
            if blocks:
                css_lines.append(blocks[0].count("\n"))

    if args.stats and ratios:
        ratios.sort()
        css_lines.sort()
        mid = ratios[len(ratios) // 2]
        print(f"info: S-1 eos-class adoption over {len(ratios)} pages — "
              f"median {mid:.2f}, min {ratios[0]:.2f}, max {ratios[-1]:.2f}")
        print(f"info: S-2 first-<style> lines over {len(css_lines)} pages — "
              f"median {css_lines[len(css_lines)//2]}, max {css_lines[-1]}")

    # S-4 / S-5 — informational worklists for migration-on-touch. Never gate,
    # and stay silent by default so preflight's advisory output stays terse.
    if args.stats or args.duplication:
        print(f"info: S-4 local esc()/escAttr() — {len(dup_esc)} page(s) "
              f"(global esc/escAttr + EOS_UI.esc already exist)")
        print(f"info: S-5 raw fetch() with no EOS.api — {len(dup_api)} page(s)")
    if args.duplication:
        for page in sorted(dup_esc):
            rel = page.relative_to(ROOT).as_posix()
            print(f"  S-4 {rel} — drop the local esc(); use the global helper")
        for page in sorted(dup_api):
            rel = page.relative_to(ROOT).as_posix()
            print(f"  S-5 {rel} — route JSON calls through EOS.api")

    if not outliers:
        print(f"clean — scanned {len(pages)} app page(s) ({islands} brand-island, exempt)")
        return 0

    print(f"{len(outliers)} structural outlier(s) (scanned {len(pages)} page(s)):\n")
    for path, fp in sorted(outliers, key=lambda x: str(x[0])):
        rel = path.relative_to(ROOT).as_posix()
        cells = ", ".join(f"{k}={v}" for k, v in fp.items())
        print(f"  {rel} — hand-rolls the shared vocabulary ({cells}); "
              f"migrate to EOS_UI helpers (see eos-components.js)")
    return len(outliers)


# ---------- selftest ------------------------------------------------------------

_HEALTHY = """
<style>.app-specific { color: var(--text); }</style>
<button class="eos-btn">Go</button>
<script>EOS_UI.modal({}); EOS_UI.toast('x'); EOS_UI.statCards([]);</script>
"""

_OUTLIER = """
<style>
.modal-bg { position: fixed; }
.toast { position: fixed; bottom: 0; }
.hero-card { padding: 8px; }
.btn-primary { color: white; }
</style>
<button class="btn-primary">Go</button>
"""

_ISLAND = """
<style>:root { --p-bg: #000; --p-text: #fff; --p-blue: #00f; }
.modal-bg { position: fixed; } .toast {} .hero-card {}</style>
<button class="btn-primary">Go</button>
"""


def _selftest() -> int:
    fails = []
    fp = fingerprint(_HEALTHY, "\n".join(STYLE_BLOCK_RE.findall(_HEALTHY)) or _HEALTHY)
    if is_outlier(fp):
        fails.append(f"healthy page misclassified as outlier: {fp}")
    css = ".modal-bg { position: fixed; }\n.toast { position: fixed; }\n.hero-card { padding: 8px; }\n.btn-primary { color: white; }"
    fp = fingerprint(_OUTLIER, css)
    if not is_outlier(fp):
        fails.append(f"hand-rolled page not flagged: {fp}")
    island_css = ":root { --p-bg: #000; --p-text: #fff; --p-blue: #00f; }"
    if not is_island(Path("apps/x/pages/index.html"), island_css):
        fails.append("private-token :root not detected as island")

    # S-4 / S-5 duplication classifier
    for src, want, why in (
        ("function esc(s) { return s; }", True, "function esc() not detected"),
        ("const escAttr = s => s;", True, "const escAttr not detected"),
        ("EOS_UI.esc(x); el.textContent = esc(y);", False, "call sites misread as definitions"),
        ("EOS_UI.esc = function(s) { return s; };", False, "EOS_UI.esc assignment misread as local"),
    ):
        if duplication_signals(src)["local_esc"] is not want:
            fails.append(f"S-4: {why} — {src!r}")
    for src, want, why in (
        ("await fetch('/x/api/y')", True, "raw fetch with no EOS.api not flagged"),
        ("await EOS.api('/x/api/y')", False, "EOS.api-only page flagged"),
        ("await EOS.api('/a'); await fetch('/b', {stream: 1})", False,
         "page using both must not flag (streaming bypass is legitimate)"),
        ("res.json(); obj.fetch(1)", False, "method call `.fetch(` misread as raw fetch"),
    ):
        if duplication_signals(src)["no_api_wrapper"] is not want:
            fails.append(f"S-5: {why} — {src!r}")

    if fails:
        for f in fails:
            print(f"SELFTEST FAIL: {f}")
        return 1
    print("selftest ok — 11 classifier cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
