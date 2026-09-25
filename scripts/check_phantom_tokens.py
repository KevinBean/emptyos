#!/usr/bin/env python3
"""Phantom CSS custom properties — `var(--x)` where `--x` is defined nowhere.

A `var()` naming an undefined custom property, **with no fallback**, is
*invalid at computed-value time*: the browser drops the whole declaration.
Nothing errors, nothing logs, and the source reads as exemplary token
discipline — which is exactly why no existing gate can see this class.

The failure differs by property, and that is why it hid for so long:

  * a **non-inherited** property (``border-radius``, ``padding``) falls back to
    its initial value — ``border-radius: var(--radius-md)`` renders a *square*
    corner. Visible, and how this class was first caught.
  * an **inherited** property (``color``) falls back to the parent's value, so
    ``color: var(--text-dim)`` silently renders muted text at full strength.
    Nothing looks broken; the type hierarchy just quietly flattens.

Founding case (2026-09-03): ``--radius-md`` was referenced by 8 declarations in
5 live apps — including ``.store-card`` in ``store``, an ESSENTIAL app — and is
defined nowhere. ``check_hardcoded_hex`` and the DL-3 scale checks look for
*literals*, so all of them passed.

Resolution is deliberately generous, because a stingy resolver reports every
brand island as a defect. A name counts as defined when it appears in:

  * the shared platform bundle (``emptyos/web/static/*.{css,js}``),
  * any file under the same app's ``pages/`` directory,
  * any local stylesheet that app's HTML ``<link>``s — **including cross-app**,
    which is what makes the shared ``--h-*`` engineering island resolve, or
  * the file itself, incl. runtime ``style.setProperty('--x', …)``.

The link graph is unioned **per app**, not per file: a sibling ``.css``/``.js``
under ``pages/`` is loaded *by* that app's HTML and inherits its stylesheet
graph. Resolving per file instead reported 116 false positives across the
``--h-*`` family alone.

Confidence split (`.claude/rules/audits.md` § "gate only the confident half"):

  * **provable** — the name is defined *nowhere in the repo*. There is no
    reading under which this resolves. These are the findings.
  * **ambiguous** — the name IS defined somewhere, just not anywhere this file
    can reach. That may equally be a gap in the resolver above, so it is
    reported as an advisory note and never affects the exit code.

Opt out at the call site, never via a central allowlist::

    color: var(--brand-x);  /* phantom-token: ignore — set by the host page */

Sibling: ``apps/extension/dev/app-builder/runs.py`` has the same idea scoped to
its own generated diffs, against a hardcoded ``_VALID_CSS_TOKENS`` list. This
checker walks the whole tree and derives the valid set instead.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json, page_files  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "emptyos" / "web" / "static"

#: A definition: `--name:` in CSS, or a runtime `setProperty('--name', …)`.
DEF_RE = re.compile(r"--([a-zA-Z0-9_-]+)\s*:")
SETPROP_RE = re.compile(r"setProperty\(\s*['\"]--([a-zA-Z0-9_-]+)['\"]")

#: A *use with no fallback*. `var(--x, y)` always yields a value, so it is
#: valid by construction and must never be flagged.
USE_NOFALLBACK_RE = re.compile(r"var\(\s*--([a-zA-Z0-9_-]+)\s*\)")

LINK_RE = re.compile(r"""<link[^>]+href\s*=\s*["']([^"']+)["']""", re.I)

IGNORE_RE = re.compile(r"phantom-token:\s*ignore")

#: Known-invented → real, so the report names the fix rather than the finding.
#: The first four are authoritative — app-builder's own guard had already
#: learned them from generated diffs that broke. **The rest are inferred from
#: the name** and are a starting point for a human, not a safe auto-fix:
#: `--bg-2 → --bg-card` is a guess about intent, and only the person editing
#: the page can say whether the surface wanted `--bg-card` or `--bg-surface`.
SUGGEST = {
    "radius-md": "--radius",
    "bg-elev": "--bg-surface",
    "bg-elevated": "--bg-surface",
    "bg-hover": "--bg-card-hover",
    "bg-active": "--accent-tint-weak",
    "text-dim": "--text-muted",
    "text-soft": "--text-muted",
    "text-primary": "--text",
    "bg-primary": "--bg",
    "bg-secondary": "--bg-card",
    "bg-alt": "--bg-card",
    "bg-subtle": "--bg-surface",
    "bg-2": "--bg-card",
    "border-color": "--border",
    "accent-color": "--accent",
    "accent-red": "--red",
    "danger-color": "--danger",
    "shadow-sm": "--shadow",
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _defs(text: str) -> set[str]:
    return set(DEF_RE.findall(text)) | set(SETPROP_RE.findall(text))


def _app_of(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix().split("/pages/")[0]


def _shared_tokens(static: Path) -> set[str]:
    out: set[str] = set()
    for f in static.rglob("*"):
        if f.is_file() and f.suffix in (".css", ".js"):
            out |= _defs(_read(f))
    return out


def _linked_tokens(page: Path, text: str, by_name: dict[str, list[Path]],
                   static: Path) -> set[str]:
    """Tokens from local stylesheets a page <link>s. Cross-app links are the
    point — `/cable/pages/cable.css` is linked by seven engineering apps."""
    out: set[str] = set()
    for href in LINK_RE.findall(text):
        if href.startswith(("http://", "https://", "//", "data:")):
            continue
        href = href.split("?")[0].split("#")[0]
        if not href:
            continue
        if href.startswith("/static/"):
            cands = [static / href[len("/static/"):]]
        elif href.startswith("/"):
            # `/<web-prefix>/pages/x.css` — the prefix is an app id, not a path,
            # so match on basename rather than trying to map prefix → directory
            # (which would mean reading every manifest).
            #
            # Known false-NEGATIVE source, accepted deliberately: two apps with
            # a same-named stylesheet both contribute, so `known` can be wider
            # than what this page really loads. It only ever suppresses
            # findings, never invents one — the right trade for a check meant to
            # gate, where a false positive gets the whole thing disabled.
            cands = by_name.get(Path(href).name, [])
        else:
            cands = [page.parent / href]
        for c in cands:
            if c.is_file():
                out |= _defs(_read(c))
    return out


def _repo_defined(root: Path) -> set[str]:
    """Every token defined anywhere we ship — the provable/ambiguous split."""
    out: set[str] = set()
    for base in ("apps", "emptyos", "plugins"):
        d = root / base
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file() or f.suffix not in (".css", ".js", ".html"):
                continue
            if "__pycache__" in f.parts or "_retired" in f.parts:
                continue
            out |= _defs(_read(f))
    return out


def scan(root: Path | None = None, static: Path | None = None):
    # Resolved at call time, not bound as a default: a def-time default cannot
    # be redirected at a fixture tree, which leaves main() untestable.
    root = root if root is not None else ROOT
    static = static if static is not None else (root / "emptyos" / "web" / "static")
    files = page_files(root, suffixes=(".html", ".js", ".css"))
    by_name: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_name[f.name].append(f)

    shared = _shared_tokens(static)
    app_defs: dict[str, set[str]] = defaultdict(set)
    for f in files:
        app_defs[_app_of(f, root)] |= _defs(_read(f))

    app_linked: dict[str, set[str]] = defaultdict(set)
    for f in files:
        if f.suffix == ".html":
            app_linked[_app_of(f, root)] |= _linked_tokens(f, _read(f), by_name, static)

    repo_defined = _repo_defined(root)

    provable, ambiguous = [], []
    for f in files:
        text = _read(f)
        app = _app_of(f, root)
        known = shared | app_defs[app] | app_linked[app] | _defs(text)
        for i, line in enumerate(text.splitlines(), 1):
            if IGNORE_RE.search(line):
                continue
            for name in USE_NOFALLBACK_RE.findall(line):
                if name in known:
                    continue
                row = (f.relative_to(root).as_posix(), i, name)
                (ambiguous if name in repo_defined else provable).append(row)
    return files, provable, ambiguous


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="agent-cli envelope")
    ap.add_argument("--detail", action="store_true", help="list every occurrence")
    args = ap.parse_args()

    files, provable, ambiguous = scan()
    names = Counter(n for _, _, n in provable)
    pfiles = {p for p, _, _ in provable}

    if args.json:
        return emit_json(
            not provable,
            "ok" if not provable else "phantom_tokens",
            f"{len(provable)} undefined-token reference(s) across {len(pfiles)} file(s)",
            {
                "scanned": len(files),
                "provable": [{"file": p, "line": i, "token": f"--{n}",
                              "suggest": SUGGEST.get(n)} for p, i, n in provable],
                "ambiguous": [{"file": p, "line": i, "token": f"--{n}"}
                              for p, i, n in ambiguous],
            },
        )

    if provable:
        print(f"{len(provable)} reference(s) to undefined CSS tokens "
              f"across {len(pfiles)} file(s), {len(names)} token name(s).")
        print("These declarations are dropped at computed-value time — a "
              "non-inherited\nproperty falls back to its initial value, an "
              "inherited one to the parent's.\n")
        for name, count in names.most_common():
            fix = SUGGEST.get(name)
            print(f"  --{name:<20} x{count:<4}" + (f"  → use {fix}" if fix else ""))
        if args.detail:
            print()
            for p, i, n in provable:
                print(f"    {p}:{i}  --{n}")

    if ambiguous:
        anames = sorted({n for _, _, n in ambiguous})
        print(f"\nnote: {len(ambiguous)} advisory — defined somewhere in the repo "
              f"but not reachable\n      from the using file "
              f"({', '.join('--' + n for n in anames)}). May be a resolver gap; "
              "never gates.")

    if provable:
        print("\nFix: use a token from theme.css, or mark a deliberate case at "
              "the call site\n     with  /* phantom-token: ignore — <why> */")

    # Preflight surfaces a check's LAST line, so end on the verdict in both
    # directions — not on the opt-out hint or the trailing advisory note.
    if provable:
        print(f"\n{len(provable)} undefined-token reference(s) in {len(pfiles)} "
              f"file(s) — these declarations are silently dropped.")
    else:
        print(f"\nclean — scanned {len(files)} page file(s), every var(--x) resolves")
    return 1 if provable else 0


if __name__ == "__main__":
    raise SystemExit(main())
