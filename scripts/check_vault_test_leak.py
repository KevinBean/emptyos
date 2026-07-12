#!/usr/bin/env python3
"""Scan (and optionally purge) leaked test fixtures from a vault.

EmptyOS's E2E suite runs against the live daemon on ``:9000``, which is
mounted on the *real* vault (``notes.path`` in ``emptyos.toml``). Every test
that creates vault-backed data writes a ``TEST_PREFIX`` ("PLAYWRIGHT-TEST-")
fixture into that real vault and relies on the conftest cleanup sweep to remove
it. The sweep is best-effort and per-app — apps it doesn't cover (cad outputs,
some bookme paths, KB reader-note pollution) leak silently and accumulate. A
2026-05-30 vault sort purged **6,954** such artifacts.

This is the guard so it can't recur. It classifies every ``TEST_PREFIX`` hit by
shape and only auto-removes the high-confidence ones:

  owned    A whole note/artifact created by a test — the prefix appears in the
           filename or in a frontmatter identity field (id/name/title/slug/...).
           Safe to delete the file (or, under ``outputs/<id>/`` or
           ``projects/<id>/``, the whole artifact dir + its non-prefixed sidecars
           such as ``milestone-log.md``).
  strip    A single self-contained list line carrying the prefix, appended into a
           real note by a task/capture/journal test — ``- [ ] PLAYWRIGHT-TEST-...``,
           ``- **05:46** 🙂 PLAYWRIGHT-TEST-...``, ``1. PLAYWRIGHT-TEST-...``. Safe
           to strip the one line; the surrounding real entries are untouched.
  review   Prefix on a NON-list line — a heading, paragraph, blockquote, or
           multi-line bullet whose marker line carries no prefix (e.g. KB
           "## Reader notes" ``> quote`` / indented-insight pairs). NEVER
           auto-edited; reported for a human, so a real note is never corrupted.

Documentation that merely *describes* the leak (devlogs + session briefs under
``10_Projects/emptyos/log/``) is excluded entirely — those legitimately mention
the prefix.

Matching is case-INSENSITIVE (fixtures write both ``PLAYWRIGHT-TEST-`` and
``playwright-test-``), and the walk covers ALL files, not just ``.md`` —
geo-cad leaks ``.geojson`` layers, and any directory whose own name carries
the prefix (``PLAYWRIGHT-TEST-proj-<ts>/``) is one owned unit deleted whole.
The prefix is a reserved namespace, so the substring match is safe by
convention; ``_DOC_PATHS`` still shields incident documentation.

Pure file I/O + tomllib. Does NOT import ``emptyos.kernel`` (no syslog handle,
safe to run while the daemon is up — see ``.claude/rules/daemon-handling.md``).

Usage::

    python scripts/check_vault_test_leak.py                 # report against emptyos.toml vault
    python scripts/check_vault_test_leak.py --purge          # remove owned + strip task lines
    python scripts/check_vault_test_leak.py --vault D:/Other  # explicit vault
    python scripts/check_vault_test_leak.py --json           # machine-readable

Exit code is the number of *review* (manual) findings, so CI / a release gate
can treat "needs a human" as a hard failure while auto-purgeable leaks return 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PREFIX = "PLAYWRIGHT-TEST-"

# Dirs never walked.
_SKIP_DIRS = {".git", ".obsidian", ".trash", ".stfolder", "node_modules"}

# Path fragments (forward-slash) under which a prefix mention is documentation,
# not a leak. Devlogs + session briefs describe the incident verbatim.
_DOC_PATHS = (
    "10_Projects/emptyos/log/",
    # AI-authored analysis outputs — a report that *describes* test pollution
    # ("wheel skewed by test-breadcrumbs") mentions the prefix as narrative,
    # not as leaked data. Prefixed FILENAMES in these dirs are still caught
    # (the scan-loop guard only skips unprefixed names).
    "30_Resources/EmptyOS/insights/outputs/",
    "30_Resources/EmptyOS/work/outputs/",
    "30_Resources/EmptyOS/worklog/calculations/",
)

# Frontmatter fields whose value, if it carries the prefix, marks the whole
# note as a test artifact. Kept broad on purpose — test fixtures stamp the
# prefix into whatever their identity field is.
_IDENTITY_FIELDS = {
    "id", "name", "title", "slug", "company", "persona",
    "event_type", "event_name", "cad_id", "project_id", "study", "label",
    # Generated-artifact identity fields — a quality-gate/scan record whose
    # asset_id/scan_id is a test asset is a whole-file test artifact, not
    # in-note pollution (was mis-classified as "review"). No real note carries
    # the test prefix in these, so promoting them to owned is safe.
    "asset_id", "scan_id", "check_id",
}

# Container dirs whose ``<container>/<id>/`` subdir is ONE artifact unit — when a
# prefixed note sits at ``<container>/<id>/<id>.md``, the whole ``<id>/`` dir is
# removed so non-prefixed sidecars (cad's document.json/model.stl,
# vault_project_*'s milestone-log.md) go with it. ``projects`` here is the
# vault_project_* layout under ``30_Resources/EmptyOS/<app>/projects/<id>/`` —
# NOT the PARA ``10_Projects/`` tree (whose parent dir is named "10_Projects",
# so real user projects never match).
_ARTIFACT_CONTAINERS = {"outputs", "projects"}

# A self-contained list item — bullet (``-``/``*``) or numbered (``1.``). A
# blockquote (``>``) is deliberately excluded: KB reader-note pollution puts the
# prefix on ``> quote`` / indented-insight lines under a clean ``- **date**``
# header, so stripping them would orphan the header — those go to review.
_STRIP_LINE = re.compile(r"^\s*(?:[-*]|\d+\.)\s")
_FM_FIELD = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


@dataclass
class Leaks:
    prefix: str
    owned: list[Path] = field(default_factory=list)          # whole-file artifacts
    owned_dirs: list[Path] = field(default_factory=list)     # artifact dirs (outputs/<id>/, projects/<id>/)
    strip_lines: dict[Path, list[int]] = field(default_factory=dict)  # path -> 0-based line idxs
    review: list[tuple[Path, int, str]] = field(default_factory=list)  # (path, 1-based lineno, text)

    @property
    def total(self) -> int:
        return len(self.owned) + len(self.owned_dirs) + len(self.strip_lines) + len(self.review)


def _is_doc_path(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return any(frag in rel for frag in _DOC_PATHS)


def _prefix_only_in_code(line: str, prefix_lower: str) -> bool:
    """True when every occurrence of the prefix on this line sits in a `code span`.

    Splitting on backticks yields alternating outside/inside segments (even
    index = outside). If no *outside* segment carries the prefix, the line only
    mentions it as code — narrative, not leaked data.
    """
    if "`" not in line:
        return False
    outside = line.split("`")[::2]
    return not any(prefix_lower in seg.lower() for seg in outside)


def _frontmatter_carries_prefix(lines: list[str], prefix: str) -> bool:
    """True if any YAML frontmatter field's value contains the prefix.

    Frontmatter is the machine-written zone — a fixture that stamped the
    prefix into ANY field (identity, tracking number, notes) wrote the whole
    record; real human notes carry pollution in the *body* (list lines, table
    rows, quoted reader-notes), which keeps its strip/review handling. The
    2026-07-04 audit verified the split holds vault-wide: every
    frontmatter-hit file was a test artifact (126 haitao packages), every real
    note's hits were body-side. ``_IDENTITY_FIELDS`` stays for the
    filename-side check's documentation value, but the gate here is any-field
    on purpose — identity-field whitelisting is what let those 126 artifacts
    sit misclassified as "review" for a month.
    """
    if not lines or lines[0].strip() != "---":
        return False
    pl = prefix.lower()
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = _FM_FIELD.match(ln)
        if m and pl in m.group(2).lower():
            return True
    return False


def scan(vault: Path, prefix: str = DEFAULT_PREFIX) -> Leaks:
    """Walk every file under *vault* and classify prefix hits by shape.

    Dirs are visited before their contents (rglob), so a prefixed directory
    claims its whole subtree first and nothing inside it is double-counted.
    """
    leaks = Leaks(prefix=prefix)
    vault = vault.resolve()
    seen_dirs: set[Path] = set()
    pl = prefix.lower()

    for p in vault.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(vault))
        # Doc/report paths: narrative prefix mentions are expected — but a
        # prefixed FILENAME there is still a real artifact, so only skip
        # unprefixed names.
        if _is_doc_path(rel) and pl not in p.name.lower():
            continue
        # Anything under an already-owned dir goes with the whole-tree delete.
        if seen_dirs and any(d in p.parents for d in seen_dirs):
            continue

        # Prefixed DIRECTORY (geo-cad PLAYWRIGHT-TEST-proj-<ts>/) — one owned unit.
        if p.is_dir():
            if pl in p.name.lower():
                seen_dirs.add(p)
                leaks.owned_dirs.append(p)
            continue

        # Non-markdown file — filename-only match (geo-cad .geojson layers).
        if p.suffix.lower() != ".md":
            if pl in p.name.lower():
                leaks.owned.append(p)
            continue

        md = p
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if pl not in text.lower() and pl not in md.name.lower():
            continue
        lines = text.splitlines()

        # Shape 1 — owned artifact (filename or frontmatter identity).
        if pl in md.name.lower() or _frontmatter_carries_prefix(lines, prefix):
            parent = md.parent
            # An ``outputs/<id>/`` or ``projects/<id>/`` artifact dir is one unit —
            # remove the dir so siblings (cad document.json/model.stl,
            # vault_project_* milestone-log.md) go with it.
            if parent.name and parent.parent.name in _ARTIFACT_CONTAINERS:
                if parent not in seen_dirs:
                    seen_dirs.add(parent)
                    leaks.owned_dirs.append(parent)
            else:
                leaks.owned.append(md)
            continue

        # Shapes 2 + 3 — prefix lines inside an otherwise-real note.
        strip_idxs: list[int] = []
        in_fence = False
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if pl not in ln.lower():
                continue
            # Prose that *quotes* the prefix as narrative — a weekly review
            # noting "test breadcrumbs skewed the wheel" — always backticks it
            # or sits in a fence. Leaked data is written by an app as bare
            # text, never as code. Same intent as _DOC_PATHS, applied per-line
            # so it also shields human notes (journals) those paths don't cover.
            if in_fence or _prefix_only_in_code(ln, pl):
                continue
            if _STRIP_LINE.match(ln):
                strip_idxs.append(i)
            else:
                leaks.review.append((md, i + 1, ln.strip()))
        if strip_idxs:
            leaks.strip_lines[md] = strip_idxs

    return leaks


def purge(leaks: Leaks, *, owned: bool = True, lines: bool = True) -> dict:
    """Remove leaked artifacts. *review* items are NEVER touched.

    ``owned`` deletes whole test-created files/artifact-dirs (zero-risk).
    ``lines`` strips single test list-lines from real notes (low-risk; touches
    personal notes like daily journals, so callers may want owned-only).

    Returns a summary dict of what was actually changed.
    """
    import shutil

    summary = {"files_deleted": 0, "dirs_deleted": 0, "lines_stripped": 0,
               "notes_edited": 0, "review_skipped": len(leaks.review)}

    if owned:
        for f in leaks.owned:
            try:
                f.unlink()
                summary["files_deleted"] += 1
            except Exception:
                pass
        for d in leaks.owned_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
                summary["dirs_deleted"] += 1
            except Exception:
                pass

    if not lines:
        return summary

    for path, idxs in leaks.strip_lines.items():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            drop = set(idxs)
            kept = [ln for i, ln in enumerate(lines) if i not in drop]
            path.write_text("".join(kept), encoding="utf-8")
            summary["lines_stripped"] += len(idxs)
            summary["notes_edited"] += 1
        except Exception:
            pass

    return summary


def _report(leaks: Leaks, vault: Path) -> None:
    v = vault.resolve()

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(v)).replace("\\", "/")
        except Exception:
            return str(p)

    if leaks.total == 0:
        print(f"✓ no '{leaks.prefix}' leaks under {v}")
        return

    if leaks.owned or leaks.owned_dirs:
        print(f"\n── owned artifacts (auto-purge: delete) — {len(leaks.owned) + len(leaks.owned_dirs)}")
        for d in leaks.owned_dirs:
            print(f"   [dir]  {rel(d)}/")
        for f in leaks.owned:
            print(f"   [file] {rel(f)}")

    if leaks.strip_lines:
        n = sum(len(v2) for v2 in leaks.strip_lines.values())
        print(f"\n── test list-lines in real notes (auto-purge: strip line) — {n} line(s) in {len(leaks.strip_lines)} note(s)")
        for path, idxs in sorted(leaks.strip_lines.items(), key=lambda kv: -len(kv[1])):
            print(f"   {rel(path)} — {len(idxs)} line(s)")

    if leaks.review:
        # Group by file so a polluted journal doesn't dump hundreds of lines.
        by_file: dict[Path, list[tuple[int, str]]] = {}
        for path, lineno, text in leaks.review:
            by_file.setdefault(path, []).append((lineno, text))
        print(f"\n── needs manual review (NOT auto-edited) — {len(leaks.review)} line(s) in {len(by_file)} note(s)")
        for path, hits in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
            print(f"   {rel(path)} — {len(hits)} line(s)")
            for lineno, text in hits[:3]:
                snippet = text[:70] + ("…" if len(text) > 70 else "")
                print(f"       :{lineno}  {snippet}")
            if len(hits) > 3:
                print(f"       … +{len(hits) - 3} more")
        print("\n   ^ prefix on non-list lines (headings/prose/quote pairs). Strip by hand so the note isn't corrupted.")


def resolve_vault(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    repo = Path(__file__).resolve().parent.parent
    cfg = repo / "emptyos.toml"
    if cfg.exists():
        try:
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            p = (data.get("notes") or {}).get("path") or ""
            if p:
                return Path(p)
        except Exception:
            pass
    raise SystemExit("could not resolve vault path — pass --vault")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan/purge leaked test fixtures from a vault.")
    ap.add_argument("--vault", help="vault root (default: emptyos.toml notes.path)")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"test marker (default: {DEFAULT_PREFIX})")
    ap.add_argument("--purge", action="store_true", help="delete owned artifacts + strip test list-lines")
    ap.add_argument("--owned-only", action="store_true",
                    help="with --purge: only delete owned artifacts, leave real notes untouched")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    vault = resolve_vault(args.vault)
    if not vault.exists():
        raise SystemExit(f"vault not found: {vault}")

    leaks = scan(vault, args.prefix)

    if args.purge:
        summary = purge(leaks, owned=True, lines=not args.owned_only)
        if args.json:
            print(json.dumps({"action": "purge", "vault": str(vault), **summary}, indent=2))
        else:
            print(f"purged under {vault}:")
            print(f"  files deleted        : {summary['files_deleted']}")
            print(f"  artifact dirs deleted: {summary['dirs_deleted']}")
            print(f"  list lines stripped  : {summary['lines_stripped']} (in {summary['notes_edited']} note(s))")
            print(f"  needs manual review  : {summary['review_skipped']}")
            if leaks.review:
                print()
                _report(Leaks(prefix=leaks.prefix, review=leaks.review), vault)
        return len(leaks.review)

    if args.json:
        v = vault.resolve()
        rel = lambda p: str(p.relative_to(v)).replace("\\", "/") if str(p).startswith(str(v)) else str(p)
        print(json.dumps({
            "vault": str(vault),
            "prefix": leaks.prefix,
            "owned": [rel(p) for p in leaks.owned],
            "owned_dirs": [rel(p) for p in leaks.owned_dirs],
            "strip_lines": {rel(p): idxs for p, idxs in leaks.strip_lines.items()},
            "review": [{"path": rel(p), "line": n, "text": t} for p, n, t in leaks.review],
            "total": leaks.total,
        }, indent=2))
    else:
        _report(leaks, vault)
        if leaks.total and not args.purge:
            print("\nrun with --purge to remove owned artifacts + strip task lines (review items stay).")

    return len(leaks.review)


if __name__ == "__main__":
    sys.exit(main())
