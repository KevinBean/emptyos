#!/usr/bin/env python3
"""Scan Claude-Code auto-memory for rot — the deterministic (L1) half of the
memory-consolidation self-audit loop (see .claude/rules/self-audit-loops.md).

Background: the session memory at ``~/.claude/projects/<slug>/memory/`` is one
fact per ``.md`` file (frontmatter ``name:`` + a body) plus a curated
``MEMORY.md`` index (and ``MEMORY-archive.md`` for shipped facts). Unlike
OpenAI's "dreaming" — which auto-rewrites memory in a black box — this loop
*surfaces* rot and leaves the fix to a human (the L2 consolidation pass /
``/preflight``), preserving the audit trail. This script is the L1 backbone:
provable, deterministic findings only. The LLM-judged classes (contradiction,
supersession, semantic staleness) are L2 and live elsewhere.

It benchmarks the memory corpus against three ground truths:
  - the REPO  — do code anchors named in a memory still exist on disk?
  - the INDEX — does every MEMORY.md pointer resolve, and is every file indexed?
  - the LINKS — internal ``[[wikilink]]`` graph (advisory — dangling forward
                links are explicitly allowed by the memory convention).

Finding classes (severity → counts toward exit code?):
  dead-anchor        high  yes   a repo source path/symbol in a body is gone
  index-dead-pointer high  yes   a MEMORY.md line points to a missing file
  index-orphan       low   no    a memory file no index line references (advisory:
                                  MEMORY.md is curated + truncated, archive splits)
  waiting-stale      med   no    a "awaiting decision"/"likely soon"/past by-date
                                  phrase that *may* be resolved — needs a human
  dangling-link      info  no    a [[x]] resolving to no memory (forward-marker; OK)

Exit code = number of HIGH-confidence findings (dead-anchor + index-dead-pointer),
mirroring check_vault_test_leak.py so a release/CI gate can treat provable rot as
a hard failure while advisory rot returns 0. Gate=False in preflight by default —
these are surfaced for the human, not auto-blocking.

Pure file I/O + stdlib. Does NOT import emptyos.kernel (no syslog handle; safe to
run while the daemon is up — see .claude/rules/daemon-handling.md). No edits: this
is a scanner. Reconciliation goes through propose→preview→confirm (L2), never an
auto-rewrite.

Usage::

    python scripts/check_memory_rot.py                  # report against this repo's memory dir
    python scripts/check_memory_rot.py --json           # machine-readable (for L2)
    python scripts/check_memory_rot.py --mem-dir PATH    # explicit memory dir
    python scripts/check_memory_rot.py --all             # show advisory classes too (default: hide info)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from md_frontmatter import parse_frontmatter  # co-located scripts/ sibling

REPO = Path(__file__).resolve().parents[1]

INDEX_FILES = {"MEMORY.md", "MEMORY-archive.md"}

# A repo-rooted source path: starts with a known top-level repo dir, ends in a
# source extension. Deliberately EXCLUDES data/ (gitignored runtime state) and
# vault dirs (30_Resources/, 10_Projects/, 50_Journal/ — those live in the vault,
# not the repo). An optional ``::symbol`` suffix is captured for a presence check.
_REPO_DIRS = "apps|emptyos|engines|plugins|scripts|docs|tests|services|profiles"
# Longest-first so ``.json`` isn't truncated to ``.js`` (lazy body + alternation
# order); the trailing ``(?![\w])`` is belt-and-suspenders against the same bug.
_EXT = "py|json|jsx|js|tsx|ts|css|html|toml|sh|bat|cfg|txt|yaml|yml"
ANCHOR_RE = re.compile(
    rf"(?<![\w./-])((?:{_REPO_DIRS})/[\w./-]+?\.(?:{_EXT}))(::[\w.]+)?(?![\w])"
)

# A line that *documents* a removal/migration legitimately names the now-dead
# path (changelog bullets "X — deleted", "renamed X -> Y", "promoted from X",
# "X is silently never committed"). That is correct memory-keeping, not rot — so
# such a dead anchor is demoted to an info-level ``historical-anchor`` rather than
# counting toward the high-confidence gate. A present-tense claim ("currently the
# auto-loop IS X") carries no marker and stays high. Tuned against real findings
# 2026-06-07 (see .claude/rules/audits.md — fix the FP shape once you've seen it).
_HIST_MARKERS = re.compile(
    r"→|->|"
    r"\b(?:deleted|removed|renamed|retired|deprecated|moved|promoted|"
    r"superseded|replaced|gone|deferred|gitignored|hallucinat\w*)\b|"
    r"never commit|no longer|used to|silently never|the old\b|"
    r"does ?n.t exist|does not exist|nonexistent",
    re.IGNORECASE,
)

# Internal memory link. Capture the target (before any ``|alias``).
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")

# MEMORY.md / archive pointers: markdown links to a sibling .md file.
INDEX_LINK_RE = re.compile(r"\]\(([\w./-]+\.md)\)")

# A past-dated commitment ("by 2026-05-01", "until ...", "deadline ...").
BY_DATE_RE = re.compile(
    r"\b(?:by|until|due|before|deadline|expires?(?:\s+(?:on|at))?)\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Literal "is this still pending?" phrases. Narrow on purpose — "deferred",
# "not built yet", "pending" alone are intentional forward-design notes and
# would drown the signal. These imply an *outcome that should have arrived*.
WAITING_PHRASES = (
    "awaiting decision", "awaiting grant", "awaiting outcome",
    "likely soon", "grant likely soon", "decision imminent", "imminent grant",
    "expected soon", "expected shortly", "decision pending",
)
# Phrases that pair a past date with a deadline/timebox context.
TIMEBOX_MARKERS = ("time-box", "timebox", "time box", "deadline", "walk-away trigger")


@dataclass
class Finding:
    cls: str
    severity: str          # high | med | low | info
    file: str              # memory filename
    detail: str
    line: int = 0


HIGH_CLASSES = {"dead-anchor", "index-dead-pointer"}
SEV_ORDER = {"high": 0, "med": 1, "low": 2, "info": 3}

# Dirs never indexed when resolving a moved anchor — VCS, caches, build output,
# gitignored runtime state, sandbox-pool members AND git worktrees (.claude/
# worktrees/*). The latter two hold full repo copies that would create phantom
# duplicate matches pointing into a throwaway tree instead of the real path. All
# dotdirs (.claude, .git, .venv, .pytest_cache, .obsidian) are skipped wholesale.
_REPO_SKIP = {"node_modules", "__pycache__", "data", "dist", "build", "venv"}


def _build_repo_index() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Index repo source files for moved-anchor resolution.

    Returns (by_suffix2, by_basename): forward-slash relpaths keyed by their
    last-two path components (e.g. ``store/app.py``) and by basename. The
    two-component suffix disambiguates common basenames (app.py, index.html).
    """
    import os

    exts = tuple("." + e for e in _EXT.split("|"))
    by_suffix2: dict[str, list[str]] = {}
    by_basename: dict[str, list[str]] = {}
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _REPO_SKIP
                   and not d.startswith(".") and not d.startswith("sandbox-")]
        for n in names:
            if not n.endswith(exts):
                continue
            rel = Path(root, n).relative_to(REPO).as_posix()
            by_basename.setdefault(n, []).append(rel)
            parts = rel.split("/")
            if len(parts) >= 2:
                by_suffix2.setdefault("/".join(parts[-2:]), []).append(rel)
    return by_suffix2, by_basename


def _resolve_moved(pathpart: str, by_suffix2: dict, by_basename: dict) -> list[str]:
    """Best-effort: where did a now-missing repo path move to? [] if truly gone."""
    parts = pathpart.split("/")
    if len(parts) >= 2:
        hit = by_suffix2.get("/".join(parts[-2:]))
        if hit:
            return hit
    return by_basename.get(parts[-1], [])


@dataclass
class Report:
    mem_dir: Path
    findings: list[Finding] = field(default_factory=list)
    n_files: int = 0
    n_anchors: int = 0

    @property
    def high(self) -> int:
        return sum(1 for f in self.findings if f.cls in HIGH_CLASSES)


def resolve_mem_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    # ~/.claude/projects/<repo-path-with-separators-dashed>/memory/
    slug = re.sub(r"[:\\/]", "-", str(REPO))
    cand = Path.home() / ".claude" / "projects" / slug / "memory"
    if cand.exists():
        return cand
    # Fallback: a single projects/*/memory dir if there's exactly one.
    proj = Path.home() / ".claude" / "projects"
    if proj.exists():
        hits = [p for p in proj.glob("*/memory") if p.is_dir()]
        if len(hits) == 1:
            return hits[0]
    raise SystemExit(f"could not locate memory dir (tried {cand}); pass --mem-dir")


def scan(mem_dir: Path) -> Report:
    mem_dir = mem_dir.resolve()
    rep = Report(mem_dir=mem_dir)
    today = date.today().isoformat()

    files = sorted(p for p in mem_dir.glob("*.md"))
    fact_files = [p for p in files if p.name not in INDEX_FILES]
    by_suffix2, by_basename = _build_repo_index()

    # ── Build the resolvable universe (for link + index checks) ──────────
    by_stem: dict[str, Path] = {p.stem: p for p in fact_files}
    names: set[str] = set()
    raw_text: dict[Path, str] = {}
    for p in files:
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        raw_text[p] = t
        if p.name not in INDEX_FILES:
            nm = parse_frontmatter(t)[0].get("name")
            if nm:
                names.add(nm)
    resolvable = set(by_stem) | names
    rep.n_files = len(fact_files)

    # ── Index pointers (MEMORY.md + MEMORY-archive.md) ───────────────────
    indexed: set[str] = set()           # filenames referenced by an index
    for idx_name in INDEX_FILES:
        idx = mem_dir / idx_name
        t = raw_text.get(idx)
        if t is None:
            continue
        for i, ln in enumerate(t.splitlines(), 1):
            for tgt in INDEX_LINK_RE.findall(ln):
                indexed.add(tgt)
                # dead pointer: link to a file that doesn't exist
                if not (mem_dir / tgt).exists():
                    rep.findings.append(Finding(
                        "index-dead-pointer", "high", idx_name,
                        f"-> {tgt} (missing)", i))

    # orphans: a fact file no index references (advisory)
    for p in fact_files:
        if p.name not in indexed:
            rep.findings.append(Finding(
                "index-orphan", "low", p.name, "no MEMORY.md/-archive pointer"))

    # ── Per-file: anchors, wikilinks, waiting phrases ────────────────────
    for p in fact_files + [mem_dir / n for n in INDEX_FILES if (mem_dir / n) in raw_text]:
        t = raw_text.get(p)
        if t is None:
            continue
        is_index = p.name in INDEX_FILES
        body = t if is_index else parse_frontmatter(t)[1]
        body_lower = body.lower()
        base_line = 0 if is_index else (t[: len(t) - len(body)].count("\n"))

        for i, ln in enumerate(body.splitlines(), 1):
            lineno = base_line + i

            # dead-anchor — repo source path / ::symbol gone
            for m in ANCHOR_RE.finditer(ln):
                pathpart, sym = m.group(1), (m.group(2) or "")
                rep.n_anchors += 1
                target = REPO / pathpart
                if not target.exists():
                    moved = _resolve_moved(pathpart, by_suffix2, by_basename)
                    if moved:
                        sugg = moved[0] + (f" (+{len(moved) - 1})" if len(moved) > 1 else "")
                        rep.findings.append(Finding(
                            "moved-anchor", "med", p.name,
                            f"`{pathpart}` moved -> {sugg}", lineno))
                    elif _HIST_MARKERS.search(ln):
                        rep.findings.append(Finding(
                            "historical-anchor", "info", p.name,
                            f"`{pathpart}` (documented removal/migration)", lineno))
                    else:
                        rep.findings.append(Finding(
                            "dead-anchor", "high", p.name,
                            f"`{pathpart}{sym}` gone (no match in repo)", lineno))
                elif sym:
                    symname = sym[2:]
                    try:
                        if symname not in target.read_text(encoding="utf-8", errors="ignore"):
                            rep.findings.append(Finding(
                                "dead-anchor", "high", p.name,
                                f"`{pathpart}` exists but symbol `{symname}` gone", lineno))
                    except Exception:
                        pass

            # wikilinks — advisory (forward-markers allowed)
            if not is_index:
                for tgt in WIKILINK_RE.findall(ln):
                    tgt = tgt.strip()
                    if tgt and tgt not in resolvable:
                        rep.findings.append(Finding(
                            "dangling-link", "info", p.name, f"[[{tgt}]]", lineno))

            # past-dated commitment
            for m in BY_DATE_RE.finditer(ln):
                if m.group(1) < today:
                    rep.findings.append(Finding(
                        "waiting-stale", "med", p.name,
                        f"'{m.group(0)}' is past — outcome recorded?", lineno))

        # waiting phrases (whole-file scan, line-agnostic)
        for ph in WAITING_PHRASES:
            if ph in body_lower:
                rep.findings.append(Finding(
                    "waiting-stale", "med", p.name,
                    f"'{ph}' — verify still pending"))
        # timebox marker + a past ISO date anywhere in the body
        if any(mk in body_lower for mk in TIMEBOX_MARKERS):
            past = [d for d in ISO_DATE_RE.findall(body) if d < today]
            if past:
                rep.findings.append(Finding(
                    "waiting-stale", "med", p.name,
                    f"timebox/deadline w/ past date(s) {', '.join(sorted(set(past))[:3])}"))

    return rep


def _print(rep: Report, show_info: bool) -> None:
    groups: dict[str, list[Finding]] = {}
    for f in rep.findings:
        if f.severity == "info" and not show_info:
            continue
        groups.setdefault(f.cls, []).append(f)

    print(f"memory rot scan — {rep.mem_dir}")
    print(f"  {rep.n_files} fact files · {rep.n_anchors} code anchors checked\n")

    if not any(f.cls in HIGH_CLASSES for f in rep.findings) and not groups:
        print("✓ no rot found")
        return

    order = ["dead-anchor", "index-dead-pointer", "moved-anchor", "waiting-stale",
             "index-orphan", "historical-anchor", "dangling-link"]
    titles = {
        "dead-anchor": "DEAD CODE ANCHORS (high — gone, no match in repo)",
        "index-dead-pointer": "DEAD INDEX POINTERS (high — fix MEMORY.md)",
        "moved-anchor": "MOVED ANCHORS (med — path stale, file relocated)",
        "waiting-stale": "STALE-CANDIDATE (med — verify outcome, then update)",
        "index-orphan": "UN-INDEXED FILES (low — add a MEMORY.md line)",
        "historical-anchor": "HISTORICAL ANCHORS (info — documents a removal/migration; OK)",
        "dangling-link": "FORWARD LINKS (info — [[x]] not yet written; OK)",
    }
    for cls in order:
        items = groups.get(cls)
        if not items:
            continue
        print(f"── {titles[cls]} — {len(items)}")
        for f in items[:40]:
            loc = f":{f.line}" if f.line else ""
            print(f"   {f.file}{loc}  {f.detail}")
        if len(items) > 40:
            print(f"   … +{len(items) - 40} more")
        print()

    print(f"{rep.high} high-confidence finding(s) (exit code). "
          "Reconcile via L2 / by hand — never auto-rewrite (audit trail).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan Claude-Code auto-memory for rot (L1).")
    ap.add_argument("--mem-dir", help="memory dir (default: ~/.claude/projects/<repo>/memory)")
    ap.add_argument("--json", action="store_true", help="machine-readable (for the L2 pass)")
    ap.add_argument("--all", action="store_true", help="include info-level (dangling-link) findings")
    args = ap.parse_args(argv)

    mem_dir = resolve_mem_dir(args.mem_dir)
    if not mem_dir.exists():
        raise SystemExit(f"memory dir not found: {mem_dir}")

    rep = scan(mem_dir)

    if args.json:
        print(json.dumps({
            "mem_dir": str(mem_dir),
            "n_files": rep.n_files,
            "n_anchors": rep.n_anchors,
            "high": rep.high,
            "findings": [
                {"cls": f.cls, "severity": f.severity, "file": f.file,
                 "detail": f.detail, "line": f.line}
                for f in sorted(rep.findings, key=lambda x: (SEV_ORDER[x.severity], x.file))
            ],
        }, indent=2, ensure_ascii=False))
    else:
        _print(rep, show_info=args.all)

    return rep.high


if __name__ == "__main__":
    sys.exit(main())
