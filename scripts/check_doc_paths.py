#!/usr/bin/env python3
"""Doc-path drift — a backticked repo path cited in prose that no longer resolves.

Docs cite code by path constantly (`apps/<id>/app.py`, `emptyos/sdk/foo.py`,
`scripts/check-x.py`). Nothing checked those citations, so the app-track
reorganisation (`apps/<id>/` -> `apps/<track>/<group>/<id>/`) silently invalidated
hundreds of them. A stale path is worse than a missing one: it reads as
authoritative and sends the next reader — or the next agent — to a file that
isn't there. Found by hand 2026-08-18 in one skill (4 stale paths in one file),
which is what earned this checker per `.claude/rules/audits.md`.

Two bands, deliberately separated because only one of them is unambiguous:

  renamed     the cited path is gone AND exactly one existing path has the same
              tail under the same top-level dir. That is a rename, provably, so
              it carries a suggested fix and `--fix` applies it.

  unresolved  the path is gone and nothing uniquely matches. Legitimate reasons
              exist — a deleted app, a deliberately prospective reference ("build
              `scripts/x.py` when…"), a negative example ("never name it
              `scripts/_common.py`"), or a rejected layout described in prose
              (store.md's `apps/installed`). Reported, never fixed, never gated.

  generated   the citation lives in a fully generated doc, so rewriting it there
              would be undone by the next regeneration. Reported with the
              generator and its real source, never fixed in place.

Never a defect, so never reported: glob/wildcard forms, `<placeholder>` and
`{template}` segments, elided paths (`...` / `…`), and citation locators
(`file.py:func`, `file.py:120`, `file.py:347-507`) which are stripped before the
existence test.

Advisory by design (`gate=False`). The unresolved band needs prose judgment, and
`.claude/rules/audits.md` is explicit that an ambiguous signal must never gate.
The `renamed` band *is* confident enough to gate — do that once its backlog is
cleared, not while it is red on arrival.

Opt out at the call site, not in a central allowlist (audits.md again — an
allowlist turns every new legitimate case into a build break):

    <!-- doc-paths: ignore -->                  same line, or the line above
    <!-- doc-paths: ignore apps/gone/app.py -->  only that path

Deliberately does NOT import `emptyos.sdk` — importing anything under that
package runs its `__init__` and pulls in `base_app`, which a standalone scanner
must not do.

Usage:
    python scripts/check_doc_paths.py [--json] [--fix] [--band renamed|unresolved|all]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Canonical prose only. `.agent-bus/` and `.agents/` are generated mirrors — fix
# the canonical copy and re-sync, or the same finding is reported three times.
SCAN_DIRS = (".claude/rules", ".claude/skills", "docs")
SCAN_FILES = ("CLAUDE.md", "AGENTS.md")

# Fully generated from another source, so rewriting them is futile — the next
# regeneration restores the stale path. These findings get their own band naming
# the generator, so the reader fixes the source instead. `docs/DESIGN.md` is
# deliberately NOT here: only one theme section is generated into it, the prose
# (and its path citations) is hand-authored and genuinely fixable in place.
GENERATED_DOCS = {
    "docs/APPS.md": "scripts/generate_apps_doc.py (source: app manifests)",
    "docs/TIERS.md": "scripts/generate_tiers_doc.py (source: release.toml)",
    "docs/SKILLS.md": "scripts/generate_skills_doc.py (source: each SKILL.md description)",
}

# Top-level dirs whose contents are real repo paths worth checking.
ROOTS = (
    "apps", "emptyos", "scripts", "plugins", "engines", "tests",
    "products", "services", "tools", "skills", ".claude", ".agent-bus", ".agents",
)

BACKTICKED = re.compile(r"`([^`\n]+)`")
PATH_PREFIX = re.compile(r"^(?:" + "|".join(re.escape(r) for r in ROOTS) + r")/")
GLOBBY = re.compile(r"[*?\[\]]")
PLACEHOLDER = re.compile(r"<[^>]*>|\{[^}]*\}")
ELIDED = ("...", "…")
# Trailing citation locators: `:func`, `:func()`, `:120`, `:347-507`.
# The paren group is `\(?\)?` not `\(\)?` on purpose: `rstrip(")")` above runs
# first and can leave a dangling `(`, which is how `plugin.py:auto_start(` reached
# the existence test as a literal path before a test pinned it.
LOCATOR = re.compile(r":[A-Za-z_][A-Za-z0-9_.]*(?:\(?\)?)?$|:\d+(?:-\d+)?$")
IGNORE = re.compile(r"<!--\s*doc-paths:\s*ignore\s*([^>]*?)\s*-->")


def strip_decoration(token: str) -> str:
    """Reduce a prose citation to the bare path it claims exists."""
    token = token.split("::")[0]      # emptyos/x.py::sym -> emptyos/x.py
    token = token.split(" ")[0]       # "scripts/x.py --flag" -> scripts/x.py
    token = token.split("#")[0]       # docs/X.md#anchor -> docs/X.md
    token = token.rstrip(".,;:)—")
    while True:
        shorter = LOCATOR.sub("", token)
        if shorter == token:
            break
        token = shorter
    return token.rstrip("/")


def unique_rename(cand: str) -> str | None:
    """Find the single existing path sharing `cand`'s tail under the same root.

    `apps/journal/app.py` -> `apps/public/standard/journal/app.py` when exactly
    one such file exists. Returns None when zero or several match — ambiguity is
    the unresolved band's problem, not something to guess at.
    """
    parts = cand.split("/")
    if len(parts) < 2:
        return None
    root, tail = parts[0], parts[1:]
    base = REPO / root
    if not base.is_dir():
        return None
    named, rest = tail[0], tail[1:]
    hits = []
    for depth in ("*", "*/*", "*/*/*"):
        for d in base.glob(f"{depth}/{named}"):
            target = d.joinpath(*rest) if rest else d
            if target.exists():
                hits.append(target)
    uniq = sorted({str(h.relative_to(REPO)).replace("\\", "/") for h in hits})
    return uniq[0] if len(uniq) == 1 else None


def ignored_paths(lines: list[str], idx: int) -> tuple[bool, set[str]]:
    """Markers on this line or the one above. Empty payload = ignore the line."""
    blanket, named = False, set()
    for probe in (idx, idx - 1):
        if probe < 0:
            continue
        for m in IGNORE.finditer(lines[probe]):
            payload = (m.group(1) or "").strip()
            if payload:
                named.add(payload)
            else:
                blanket = True
    return blanket, named


def iter_docs() -> list[Path]:
    docs = []
    for d in SCAN_DIRS:
        base = REPO / d
        if base.is_dir():
            docs += sorted(base.rglob("*.md"))
    for f in SCAN_FILES:
        p = REPO / f
        if p.is_file():
            docs.append(p)
    return docs


def scan() -> tuple[list[dict], list[dict], int, list[dict]]:
    renamed: list[dict] = []
    unresolved: list[dict] = []
    generated: list[dict] = []
    checked = 0
    for path in iter_docs():
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        for m in BACKTICKED.finditer(text):
            token = m.group(1).strip()
            if not PATH_PREFIX.match(token):
                continue
            cand = strip_decoration(token)
            if (
                not cand
                or GLOBBY.search(cand)
                or PLACEHOLDER.search(cand)
                or any(e in cand for e in ELIDED)
            ):
                continue
            checked += 1
            if (REPO / cand).exists():
                continue
            idx = text[: m.start()].count("\n")
            blanket, named = ignored_paths(lines, idx)
            if blanket or cand in named:
                continue
            rel = str(path.relative_to(REPO)).replace("\\", "/")
            entry = {"file": rel, "line": idx + 1, "cited": cand}
            fix = unique_rename(cand)
            if rel in GENERATED_DOCS:
                generated.append({**entry, "fix": fix, "source": GENERATED_DOCS[rel]})
            elif fix:
                renamed.append({**entry, "fix": fix})
            else:
                unresolved.append(entry)
    return renamed, unresolved, checked, generated


def apply_fixes(renamed: list[dict]) -> int:
    """Rewrite the confident band in place, longest-path-first per file.

    Longest first so `apps/x` inside `apps/x/app.py` cannot be rewritten by the
    shorter finding and strand the longer one.
    """
    by_file: dict[str, list[dict]] = {}
    for r in renamed:
        # Guard at the write, not in the caller: a generated doc must stay
        # untouched no matter which list it was handed in, because the next
        # regeneration would restore the stale path anyway.
        if r["file"] in GENERATED_DOCS:
            continue
        by_file.setdefault(r["file"], []).append(r)
    changed = 0
    for rel, entries in by_file.items():
        p = REPO / rel
        text = p.read_text(encoding="utf-8")
        before = text
        for e in sorted(entries, key=lambda x: -len(x["cited"])):
            text = text.replace(f"`{e['cited']}`", f"`{e['fix']}`")
            text = text.replace(f"`{e['cited']}/", f"`{e['fix']}/")
            text = text.replace(f"`{e['cited']}:", f"`{e['fix']}:")
            text = text.replace(f"`{e['cited']}::", f"`{e['fix']}::")
        if text != before:
            p.write_text(text, encoding="utf-8", newline="\n")
            changed += 1
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    ap.add_argument("--fix", action="store_true", help="rewrite the confident 'renamed' band")
    ap.add_argument(
        "--band", choices=("renamed", "unresolved", "generated", "all"), default="all",
        help="which band to report (default all)",
    )
    args = ap.parse_args()

    renamed, unresolved, checked, generated = scan()

    if args.fix:
        files = apply_fixes(renamed)
        msg = f"rewrote {len(renamed)} citation(s) across {files} file(s)"
        if args.json:
            return emit_json(True, "ok", msg, {"rewritten": renamed})
        print(f"OK: {msg}")
        print("    re-run without --fix to confirm, then `eos bus import` to sync mirrors")
        return 0

    show_r = args.band in ("renamed", "all")
    show_u = args.band in ("unresolved", "all")
    show_g = args.band in ("generated", "all")
    total = (
        (len(renamed) if show_r else 0)
        + (len(unresolved) if show_u else 0)
        + (len(generated) if show_g else 0)
    )
    msg = (
        f"{checked} path citations checked · {len(renamed)} stale-renamed "
        f"(fixable) · {len(unresolved)} unresolved (advisory) · "
        f"{len(generated)} in generated docs (fix the source)"
    )

    if args.json:
        return emit_json(
            total == 0, "ok" if total == 0 else "drift", msg,
            {
                "checked": checked,
                "renamed": renamed if show_r else [],
                "unresolved": unresolved if show_u else [],
                "generated": generated if show_g else [],
            },
        )

    if show_r and renamed:
        print(f"STALE (renamed — `--fix` rewrites these): {len(renamed)}")
        for r in renamed:
            print(f"  {r['file']}:{r['line']}")
            print(f"      {r['cited']}  ->  {r['fix']}")
    if show_g and generated:
        print(f"\nIN GENERATED DOCS ({len(generated)}) — rewriting these will not stick:")
        for g in generated:
            print(f"  {g['file']}:{g['line']}: {g['cited']}")
            print(f"      fix the source: {g['source']}")
    if show_u and unresolved:
        print(f"\nUNRESOLVED (advisory — needs judgment): {len(unresolved)}")
        for u in unresolved:
            print(f"  {u['file']}:{u['line']}: {u['cited']}")
    if total == 0:
        print(f"OK: {msg}")
        return 0
    print(f"\n{msg}")
    # Advisory: exit 1 only when the confident band is non-empty, so the
    # unresolved band alone can never fail a caller that opts into gating.
    return 1 if (show_r and renamed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
