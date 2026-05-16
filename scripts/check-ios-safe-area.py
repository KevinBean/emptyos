#!/usr/bin/env python3
"""Scan CSS + HTML for iOS notch / home-indicator / Safari-tap regressions.

Catches the bug families fixed on branch `claude/fix-ios-notch-layout-1H3Py`:

  N — notch overlap          fixed/sticky element pinned to top:0 / inset:0
                             without padding-top env(safe-area-inset-top)
                             or top: calc(... + env(safe-area-inset-top)).
  H — home-indicator overlap fixed/sticky element pinned to bottom:0 / inset:0
                             without padding-bottom / bottom env(safe-area-inset-bottom).
  P — hardcoded notch pad    padding-top: 44-50px in lieu of env().
  V — viewport-collapse      height/min-height: 100vh (use 100dvh so the
                             layout doesn't jump when iOS Safari hides URL bar).
  T — div-onclick tap-fail   <div|aside|section ... onclick=...> in HTML —
                             iOS Safari only fires onclick on plain divs that
                             have cursor:pointer. Prefer <button>.

Usage:
    python scripts/check-ios-safe-area.py             # scan all tracked files
    python scripts/check-ios-safe-area.py --staged    # scan only staged files
    python scripts/check-ios-safe-area.py path/to/dir # scan a subtree
    python scripts/check-ios-safe-area.py --install-hook   # write .git/hooks/pre-commit

Exit code 0 = clean, 1 = findings.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from check_base import REPO_ROOT as ROOT, git_staged, git_tracked, install_pre_commit_hook

SCAN_EXTS = {".html", ".css", ".js"}
SKIP_FRAGMENTS = (
    "/_retired/",
    "/_example/",
    "/legacy/",
    "/node_modules/",
    "/dist/",
    "/.git/",
    "/data/",
    "/tests/",
    "/.venv/",
    "scripts/check-ios-safe-area.py",
)

# CSS rule body extractor: leaf {...} blocks (no nested braces inside),
# which is what real declarations look like even when wrapped in @media.
LEAF_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.MULTILINE)
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)

POS_FIXED = re.compile(r"position\s*:\s*fixed\b")
# Opt-out: a /* eos-ios-ok */ or /* eos-ios-ok: N H V */ comment inside a rule
# body suppresses all listed codes (or all codes when no list given).
OPT_OUT = re.compile(
    # `/* eos-ios-ok */` or `/* eos-ios-ok: N H V — free-form rationale */`.
    # Captures the code list; rationale text after the list is allowed and
    # ignored (matches up to the comment terminator).
    r"/\*\s*eos-ios-ok(?:\s*:\s*([NHPVT,\s]+?))?\s*(?:[—\-][^*]*)?\*/",
    re.DOTALL,
)
TOP_ZERO = re.compile(r"(?<![\w-])top\s*:\s*0(?:px|\.0+px)?\b")
BOTTOM_ZERO = re.compile(r"(?<![\w-])bottom\s*:\s*0(?:px|\.0+px)?\b")
INSET_ZERO = re.compile(r"(?<![\w-])inset\s*:\s*0(?:px|\.0+px)?\b")
SAFE_TOP = re.compile(r"safe-area-inset-top")
SAFE_BOTTOM = re.compile(r"safe-area-inset-bottom")
HARDCODED_NOTCH_TOP = re.compile(
    # Negative lookbehind: avoid scroll-padding-top, which is unrelated.
    r"(?<!-)padding-top\s*:\s*(?:4[4-9]|50)px\b"
)
VIEWPORT_VH = re.compile(r"\b(?:min-)?height\s*:\s*[^;{}]*?\b100vh\b")
DIV_ONCLICK_TAG = re.compile(
    r"<(div|aside|section|nav|header|footer)\b([^>]*)\bonclick\s*=",
    re.IGNORECASE,
)
TAG_CLASS_ATTR = re.compile(
    r"""\bclass\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
CSS_CLASS_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)")
CURSOR_POINTER = re.compile(r"cursor\s*:\s*pointer\b")
DISPLAY_NONE = re.compile(r"display\s*:\s*none\b")
POINTER_EVENTS_NONE = re.compile(r"pointer-events\s*:\s*none\b")
# Backdrops/scrims are typically transparent overlays. We exempt rules
# whose only visible surface is a low-alpha background — they don't carry
# foreground content that would be hidden by the notch / home indicator.
BACKDROP_BG = re.compile(
    r"background\s*:\s*rgba?\([^)]*\b0?\.[0-9]+\)\s*;",  # rgba(*, *, *, 0.X)
    re.IGNORECASE,
)


@dataclass
class Finding:
    file: Path
    line: int
    code: str  # N, H, P, V, T
    msg: str

    def format(self) -> str:
        rel = self.file.relative_to(ROOT) if self.file.is_relative_to(ROOT) else self.file
        return f"{rel}:{self.line} [{self.code}] {self.msg}"


# ---------- file enumeration ---------------------------------------------------


def _walk_subtree(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def _filter(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if p.suffix not in SCAN_EXTS:
            continue
        s = "/" + str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p).replace("\\", "/")
        if any(skip in s for skip in SKIP_FRAGMENTS):
            continue
        if not p.exists():
            continue
        out.append(p)
    return out


# ---------- CSS / HTML scanning ------------------------------------------------


def _line_of(text: str, char_index: int) -> int:
    return text.count("\n", 0, char_index) + 1


def _iter_css_blocks(text: str, base_offset: int = 0):
    """Yield (start_line, selector, body) for every leaf CSS rule."""
    for m in LEAF_RULE_RE.finditer(text):
        body = m.group(2)
        # Skip @media / @supports headers — those are handled by their leaves.
        sel = m.group(1).strip()
        if sel.startswith("@"):
            continue
        line = _line_of(text, base_offset + m.start(2))
        yield line, sel, body


def _iter_all_css(path: Path, text: str):
    """For .css files yield blocks directly; for .html files yield blocks
    found inside <style> tags only (so we don't try to lint inline body content)."""
    if path.suffix == ".css":
        yield from _iter_css_blocks(text)
        return
    for sm in STYLE_BLOCK_RE.finditer(text):
        yield from _iter_css_blocks(sm.group(1), base_offset=sm.start(1))


def _opt_out_codes(body: str) -> set[str]:
    m = OPT_OUT.search(body)
    if not m:
        return set()
    listed = m.group(1)
    if not listed:
        return {"N", "H", "P", "V", "T"}
    return {c.strip() for c in listed.replace(",", " ").split() if c.strip()}


def _scan_css_rule(path: Path, line: int, sel: str, body: str) -> list[Finding]:
    out: list[Finding] = []
    if DISPLAY_NONE.search(body) and not POS_FIXED.search(body):
        # Hidden non-positioned rule — irrelevant.
        return out

    skip = _opt_out_codes(body)

    # Only flag position:fixed (true viewport-pinning). position:sticky is
    # almost always scoped to a scroll container (table headers, nested
    # panels) and produces too many false positives at scan time.
    is_pinned = bool(POS_FIXED.search(body))
    pinned_top = is_pinned and (TOP_ZERO.search(body) or INSET_ZERO.search(body))
    pinned_bottom = is_pinned and (BOTTOM_ZERO.search(body) or INSET_ZERO.search(body))
    is_backdrop = bool(BACKDROP_BG.search(body)) or bool(POINTER_EVENTS_NONE.search(body))

    if pinned_top and not SAFE_TOP.search(body) and not is_backdrop and "N" not in skip:
        out.append(
            Finding(
                path,
                line,
                "N",
                f"position: fixed pinned to top without env(safe-area-inset-top) — selector: {sel[:80]}",
            )
        )

    if pinned_bottom and not SAFE_BOTTOM.search(body) and not is_backdrop and "H" not in skip:
        out.append(
            Finding(
                path,
                line,
                "H",
                f"position: fixed pinned to bottom without env(safe-area-inset-bottom) — selector: {sel[:80]}",
            )
        )

    if HARDCODED_NOTCH_TOP.search(body) and not SAFE_TOP.search(body) and "P" not in skip:
        out.append(
            Finding(
                path,
                line,
                "P",
                f"hardcoded padding-top 44-50px without env(safe-area-inset-top) — selector: {sel[:80]}",
            )
        )

    if VIEWPORT_VH.search(body) and "V" not in skip:
        out.append(
            Finding(
                path,
                line,
                "V",
                f"100vh collapses with iOS Safari URL bar — use 100dvh — selector: {sel[:80]}",
            )
        )

    return out


def _collect_pointer_classes(files: list[Path]) -> set[str]:
    """Return set of class names that appear in at least one rule with cursor:pointer.
    Conservative: any class token in a cursor:pointer rule's selector counts."""
    out: set[str] = set()
    for p in files:
        if p.suffix not in {".css", ".html"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for _line, sel, body in _iter_all_css(p, text):
            if CURSOR_POINTER.search(body):
                for cm in CSS_CLASS_TOKEN.finditer(sel):
                    out.add(cm.group(1))
    return out


INLINE_POINTER = re.compile(r"""\bstyle\s*=\s*["'][^"']*cursor\s*:\s*pointer""", re.IGNORECASE)


def _scan_html_onclick(path: Path, text: str, pointer_classes: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for m in DIV_ONCLICK_TAG.finditer(text):
        line = _line_of(text, m.start())
        tag = m.group(1).lower()
        attrs = m.group(2)
        # Inline style="cursor:pointer" is sufficient on iOS too.
        if INLINE_POINTER.search(attrs):
            continue
        cm = TAG_CLASS_ATTR.search(attrs)
        classes = cm.group(1).split() if cm else []
        if classes and any(c in pointer_classes for c in classes):
            continue  # at least one class has cursor:pointer somewhere
        cls_hint = f' class="{" ".join(classes)}"' if classes else " (no class)"
        out.append(
            Finding(
                path,
                line,
                "T",
                f'<{tag}{cls_hint} onclick=...>: iOS Safari may drop the tap. Use <button type="button"> or set cursor:pointer.',
            )
        )
    return out


def scan_file(path: Path, pointer_classes: set[str]) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[Finding] = []
    for line, sel, body in _iter_all_css(path, text):
        out.extend(_scan_css_rule(path, line, sel, body))
    if path.suffix == ".html":
        out.extend(_scan_html_onclick(path, text, pointer_classes))
    return out


# ---------- driver -------------------------------------------------------------

CODE_NAMES = {
    "N": "notch overlap",
    "H": "home-indicator overlap",
    "P": "hardcoded notch padding",
    "V": "100vh viewport collapse",
    "T": "div-onclick tap-fail",
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--staged", action="store_true", help="scan only files staged for commit")
    p.add_argument(
        "--install-hook",
        action="store_true",
        help="install .git/hooks/pre-commit that runs --staged",
    )
    p.add_argument("paths", nargs="*", help="optional paths to scan (defaults to git-tracked)")
    args = p.parse_args()

    if args.install_hook:
        return install_pre_commit_hook(
            script="check-ios-safe-area.py",
            backup_suffix=".pre-ios.bak",
            idempotent_marker="check-ios-safe-area.py",
        )

    if args.paths:
        candidates: list[Path] = []
        for arg in args.paths:
            ap = Path(arg)
            if not ap.is_absolute():
                ap = ROOT / ap
            if ap.is_dir():
                candidates.extend(_walk_subtree(ap))
            elif ap.exists():
                candidates.append(ap)
    elif args.staged:
        candidates = git_staged()
    else:
        candidates = git_tracked()

    files = _filter(candidates)
    # First pass: build the global pointer-class index so we can suppress
    # div-onclick findings whose classes already declare cursor:pointer.
    # When the user passes --staged or a subtree, we still scan the whole
    # tree for the pointer index — otherwise we'd over-flag.
    if args.staged or args.paths:
        # Union git-tracked with the files actually being scanned, so untracked
        # paths passed on the CLI (or freshly staged files) contribute their own
        # cursor:pointer rules to the index instead of being flagged spuriously.
        tracked = _filter(git_tracked())
        seen = {p.resolve() for p in tracked}
        index_files = list(tracked)
        for p in files:
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                index_files.append(p)
    else:
        index_files = files
    pointer_classes = _collect_pointer_classes(index_files)

    findings: list[Finding] = []
    for f in files:
        findings.extend(scan_file(f, pointer_classes))

    if not findings:
        print(f"clean — scanned {len(files)} files")
        return 0

    # Group by code for a tidy report
    by_code: dict[str, list[Finding]] = {}
    for f in findings:
        by_code.setdefault(f.code, []).append(f)

    for code, group in sorted(by_code.items()):
        print(f"\n[{code}] {CODE_NAMES[code]} — {len(group)}")
        for f in sorted(group, key=lambda x: (str(x.file), x.line)):
            print(f"  {f.format()}")

    print(
        f"\n{len(findings)} finding(s) across {len({f.file for f in findings})} file(s) "
        f"(scanned {len(files)} files)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
