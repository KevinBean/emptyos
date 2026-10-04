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
  strip    A single self-contained line carrying the prefix, appended into a real
           note by a task/capture/journal test — ``- [ ] PLAYWRIGHT-TEST-...``,
           ``- **05:46** 🙂 PLAYWRIGHT-TEST-...``, ``1. PLAYWRIGHT-TEST-...`` — or a
           markdown TABLE ROW (``| 2026-08-01 | 5 | dictionary | ... |``), which is
           equally self-contained: a header and its ``|---|`` separator carry no
           prefix, and a table with zero data rows is still valid markdown. Safe
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
import os
import re
import shutil
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PREFIX = "PLAYWRIGHT-TEST-"

# Dirs never walked, plus every dot-dir (see ``_skipped_dir`` below).
#
# ``.stversions`` is Syncthing's version ARCHIVE; ``.stfolder`` — which WAS
# skipped — is only its marker dir, so the one holding files was the one being
# walked. Two consequences, and they are not the same size:
#
#   * The gate could not pass. A finding in a restore point is unactionable by
#     construction (editing it corrupts what you would restore from), and the
#     exit code IS the review count, so it sat at 8 — measured, not theoretical —
#     and an audit that can never go green gets switched off.
#   * ``purge`` runs off this same walk, and the danger is NOT the archived
#     fixture whose filename carries the prefix (deleting a backup of junk costs
#     nothing). It is an archived copy of a REAL note that a test polluted: a
#     ``- [ ] PLAYWRIGHT-TEST-…`` line classifies as ``strip``, so ``purge``
#     REWRITES the restore point — unattended, via conftest's session-end
#     backstop. Latent only because today's five archived hits all land in
#     ``review``; one polluted daily journal being versioned is the whole
#     distance to real data loss.
#
# Note the archiving happens on the SYNC PEERS, not here: a local ``unlink``
# propagates outward, and Syncthing versions a file when it replaces or deletes
# one on receiving that change. So the self-destructive cycle needs the sweep
# running on the peer too — real on a multi-device vault, and not reproducible
# on one machine.
#
# ``check_vault_structure.py`` already skips dot-dirs generically, so after this
# nothing in EmptyOS reports on ``.stversions`` at all. That is deliberate —
# Syncthing's own versioning-cleanup config is what bounds that tree — but do not
# read a clean exit as "the archive is clean".
#
# **The five dotted names below are EXPLICIT, not load-bearing.** ``_skipped_dir``
# skips any leading-dot component, so removing them changes no verdict and no test
# can pin them — the dot rule fires first, always. They are kept because each one
# records a decision a reader needs (``.stversions`` above all), and because a
# future narrowing of the dot rule would otherwise silently drop five protections
# at once. Same treatment as ``emptyos/basepath.py``'s explicit branches: stated
# here so nobody deletes them as dead code, and so nobody ADDS a dotted name
# expecting it to do something. ``node_modules`` is the only entry doing work.
_SKIP_DIRS = {".git", ".obsidian", ".trash", ".stfolder", ".stversions", "node_modules"}

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
# A markdown table DATA row: opens and closes with ``|`` and has at least two
# delimiters. Self-contained in the same sense as a list item — nothing is
# orphaned by removing it, because the header row and the ``|---|`` separator
# never carry the prefix (an app appends rows, it does not append headers), and
# a table whose data rows are all removed is still valid markdown. Added when
# speaking/dictionary tests were found appending rows to a real practice log,
# where every row could only be reported for manual review forever.
_STRIP_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _skipped_dir(rel_parts: tuple[str, ...]) -> bool:
    """True when a vault-RELATIVE path sits under a dir we never walk.

    Two things here are load-bearing, and the enumeration alone had neither.

    **Relative, not absolute.** The old check ran over ``p.parts`` — every
    component including the ones ABOVE the vault root. A vault living under
    ``~/.local/share/vault`` or any path with a ``node_modules``/``.git``
    component scanned zero files and printed "no leaks" with exit 0: the whole
    guard silently off, in the shape ``.claude/rules/audits.md`` warns about.

    **A class rule, not a list of names.** ``check_vault_structure.py`` skips
    dot-dirs generically for exactly this reason — viewer config, search
    indexes, trash, sync archives. Enumerating names meant every new hidden dir
    a plugin drops in the vault (``.smart-env``, ``.datacore``, a second sync
    folder's archive) stayed walked AND purgeable until someone noticed and
    added one more name. The dot rule subsumes five of the six entries; only
    ``node_modules`` needs naming.
    """
    return any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts)


def _is_strippable_line(line: str) -> bool:
    """True when the whole line can be dropped without orphaning anything."""
    if _STRIP_LINE.match(line):
        return True
    if not _STRIP_TABLE_ROW.match(line) or line.count("|") < 2:
        return False
    # Never touch a separator row (``|---|:--:|``) — it has no text to leak, so
    # this only fires on a malformed hand-edit, but losing it breaks the table.
    return bool(re.sub(r"[\s:|-]", "", line))


_FM_FIELD = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


@dataclass
class Leaks:
    prefix: str
    owned: list[Path] = field(default_factory=list)          # whole-file artifacts
    owned_dirs: list[Path] = field(default_factory=list)     # artifact dirs (outputs/<id>/, projects/<id>/)
    strip_lines: dict[Path, list[int]] = field(default_factory=dict)  # path -> 0-based line idxs
    review: list[tuple[Path, int, str]] = field(default_factory=list)  # (path, 1-based lineno, text)
    unread: int = 0   # notes whose metadata or content could not be read

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


def scan(vault: Path, prefix: str = DEFAULT_PREFIX, *, since: float | None = None) -> Leaks:
    """Walk every file under *vault* and classify prefix hits by shape.

    ``since`` (epoch seconds) limits CONTENT reads to notes modified at or after
    it. Names are still checked everywhere, so a prefixed file or dir is found
    whatever its age, and every note inside an ``outputs/<id>/`` or
    ``projects/<id>/`` dir is read, because an old owner note there decides
    what its fresh siblings are. conftest passes ``sweep_cutoff()``, the start
    of the last sweep that ended clean: on a cold disk the full read is
    I/O-bound (86 s for ~1 GB, against 3 s warm, measured 2026-09-28).

    A prefixed directory claims its whole subtree: it is recorded once and
    never descended, so nothing inside it is double-counted.

    The walk's shape is about cost, because conftest runs this after every
    daemon-backed pytest session. Skipped dirs are pruned before descent
    (``.git`` alone was ~9k entries), the file/dir split comes from ``scandir``
    instead of a ``stat`` per entry, and a note is decoded only when its raw
    bytes carry the prefix — a real vault is ~1 GB of markdown with a handful
    of hits. The ``rglob`` version took ~57 s on a 33k-note vault (2026-09-28).
    """
    leaks = Leaks(prefix=prefix)
    vault = vault.resolve()
    pl = prefix.lower()
    # ASCII-lowercasing the raw bytes matches what decode-then-lower matches,
    # except for text only a decode can produce: a non-ASCII char that
    # lowercases into ASCII (the Kelvin sign -> "k") or an undecodable byte
    # splitting the prefix that ``errors="ignore"`` would glue back together.
    # An app writing a fixture produces neither. A non-ASCII prefix skips the
    # prefilter and decodes every note, as before.
    pl_bytes = pl.encode("ascii") if pl.isascii() else None

    for dirpath, dirnames, filenames in os.walk(vault):
        here = Path(dirpath)
        rel_dir = os.path.relpath(dirpath, vault)
        rel_dir = "" if rel_dir == "." else rel_dir
        marks = (len(leaks.owned), len(leaks.owned_dirs),
                 len(leaks.review), len(leaks.strip_lines))

        kept = []
        for d in sorted(dirnames):
            if _skipped_dir((d,)):
                continue
            # Prefixed DIRECTORY (geo-cad PLAYWRIGHT-TEST-proj-<ts>/) — one
            # owned unit, deleted whole. Checked before the doc-path skip: a
            # prefixed name is a real artifact even under a doc path.
            if pl in d.lower():
                leaks.owned_dirs.append(here / d)
                continue
            kept.append(d)
        dirnames[:] = kept

        # Doc/report paths: narrative prefix mentions are expected — but a
        # prefixed FILENAME there is still a real artifact, so only skip
        # unprefixed names.
        doc_dir = bool(rel_dir) and _is_doc_path(rel_dir + "/")
        # ``outputs/<id>/`` or ``projects/<id>/`` — one artifact unit. Never the
        # vault root: the rglob version tested the root's own PARENT, so a vault
        # living at ``.../projects/vault`` with a prefixed note at its top level
        # recorded the whole vault as an artifact dir for purge to rmtree.
        artifact_dir = bool(rel_dir) and here.parent.name in _ARTIFACT_CONTAINERS

        for name in sorted(filenames):
            if _skipped_dir((name,)):
                continue
            prefixed_name = pl in name.lower()
            if doc_dir and not prefixed_name:
                continue
            p = here / name

            # Non-markdown file — filename-only match (geo-cad .geojson layers).
            if not name.lower().endswith(".md"):
                if prefixed_name:
                    leaks.owned.append(p)
                continue

            md = p
            if not prefixed_name and since is not None and not artifact_dir:
                try:
                    if os.stat(md).st_mtime < since:
                        continue
                except OSError:
                    leaks.unread += 1
                    continue
            if not prefixed_name and pl_bytes is not None:
                try:
                    if pl_bytes not in md.read_bytes().lower():
                        continue
                except Exception:
                    leaks.unread += 1
                    continue
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                leaks.unread += 1
                continue
            if pl not in text.lower() and not prefixed_name:
                continue
            lines = text.splitlines()

            # Shape 1 — owned artifact (filename or frontmatter identity).
            if prefixed_name or _frontmatter_carries_prefix(lines, prefix):
                if artifact_dir:
                    # Remove the whole dir so siblings (cad document.json /
                    # model.stl, vault_project_*'s milestone-log.md) go with it.
                    # Anything this dir already contributed is superseded by
                    # that delete, and nothing under it is walked.
                    del leaks.owned[marks[0]:]
                    del leaks.owned_dirs[marks[1]:]
                    del leaks.review[marks[2]:]
                    for superseded in list(leaks.strip_lines)[marks[3]:]:
                        del leaks.strip_lines[superseded]
                    leaks.owned_dirs.append(here)
                    dirnames[:] = []
                    break
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
                # noting "test breadcrumbs skewed the wheel" — always backticks
                # it or sits in a fence. Leaked data is written by an app as
                # bare text, never as code. Same intent as _DOC_PATHS, applied
                # per-line so it also shields human notes (journals) those
                # paths don't cover.
                if in_fence or _prefix_only_in_code(ln, pl):
                    continue
                if _is_strippable_line(ln):
                    strip_idxs.append(i)
                else:
                    leaks.review.append((md, i + 1, ln.strip()))
            if strip_idxs:
                leaks.strip_lines[md] = strip_idxs

    return leaks


# Slack between a sweep's start and the next sweep's cutoff: covers coarse
# filesystem timestamps (FAT keeps 2 s) and a write racing the scan's start.
_SWEEP_SLACK_S = 60
# A note can arrive with an OLD mtime — a sync tool or ``shutil.copy2``
# preserves the source's — and a cutoff can never see it. A full read at least
# this often bounds that blind spot, at one cold read (~1.5 min) a week.
_FULL_READ_EVERY_S = 7 * 24 * 3600


def _read_marker(marker: Path, vault: Path) -> dict | None:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("vault") != str(vault.resolve()):
        return None
    return data


def _epoch(value) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else None


def sweep_cutoff(marker: Path, vault: Path, *, now: float | None = None) -> float | None:
    """The ``since`` for the next sweep, or None (read everything).

    None when the marker is missing, unreadable, written for another vault, or
    the last clean FULL read is older than ``_FULL_READ_EVERY_S`` — every doubt
    falls back to the full read, never to skipping.
    """
    data = _read_marker(marker, vault)
    if data is None:
        return None
    since, full_at = _epoch(data.get("clean_since")), _epoch(data.get("full_at"))
    if since is None or full_at is None:
        return None
    if (time.time() if now is None else now) - full_at > _FULL_READ_EVERY_S:
        return None
    return since


def sweep_was_clean(leaks: Leaks, summary: dict) -> bool:
    """True only when the sweep left nothing a later cutoff could hide.

    Anything left behind keeps its old mtime, so advancing past it would hide
    it from every later scoped sweep: a review line, a note the scan could not
    read, a note the purge skipped, an artifact it failed to delete.
    """
    return not (leaks.review or leaks.unread
                or summary.get("notes_skipped") or summary.get("delete_failed"))


def record_clean_sweep(marker: Path, vault: Path, started: float, *, full: bool) -> None:
    """Advance the cutoff after a sweep ``sweep_was_clean`` accepted.

    ``started`` must be taken BEFORE the scan, so a write landing mid-sweep is
    read next time. ``full`` says the sweep read every note (no ``since``); a
    scoped sweep keeps the previous full read's time.
    """
    previous = _read_marker(marker, vault) or {}
    full_at = started if full else _epoch(previous.get("full_at"))
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"vault": str(vault.resolve()),
                               "clean_since": started - _SWEEP_SLACK_S,
                               "full_at": full_at}),
                   encoding="utf-8")
    os.replace(tmp, marker)


def purge(leaks: Leaks, *, owned: bool = True, lines: bool = True) -> dict:
    """Remove leaked artifacts. *review* items are NEVER touched.

    ``owned`` deletes whole test-created files/artifact-dirs (zero-risk).
    ``lines`` strips single test list-lines from real notes (low-risk; touches
    personal notes like daily journals, so callers may want owned-only).

    Returns a summary dict of what was actually changed.
    """
    summary = {"files_deleted": 0, "dirs_deleted": 0, "lines_stripped": 0,
               "notes_edited": 0, "notes_skipped": 0, "delete_failed": 0,
               "review_skipped": len(leaks.review)}

    if owned:
        for f in leaks.owned:
            try:
                f.unlink()
                summary["files_deleted"] += 1
            except Exception:
                summary["delete_failed"] += 1
        for d in leaks.owned_dirs:
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                summary["delete_failed"] += 1
            else:
                summary["dirs_deleted"] += 1

    if not lines:
        return summary

    pl = leaks.prefix.lower()
    for path, idxs in leaks.strip_lines.items():
        # The indexes came from `scan`, and the note may have changed since —
        # most dangerously under a second purge (two pytest runs ending at
        # once). That purge truncated the file to rewrite it; a read landing in
        # that instant saw "" and, before this check, wrote "" straight back —
        # two real notes went to zero bytes on 2026-08-18. So re-read, and go on
        # only if every scanned index is STILL a test line; otherwise skip the
        # note. (A writer landing between this read and the swap is still
        # overwritten; the window is milliseconds, not the whole session.)
        target = path.resolve()   # a symlinked note: edit the note, keep the link
        try:
            with open(target, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                lines = fh.read().splitlines(keepends=True)
        except OSError:
            summary["notes_skipped"] += 1
            continue
        drop = {i for i in idxs if i < len(lines) and _still_a_test_line(lines[i], pl)}
        if drop != set(idxs):
            summary["notes_skipped"] += 1
            continue
        kept = [ln for i, ln in enumerate(lines) if i not in drop]
        if _replace_atomically(target, "".join(kept)):
            summary["lines_stripped"] += len(drop)
            summary["notes_edited"] += 1
        else:
            summary["notes_skipped"] += 1

    return summary


def _still_a_test_line(line: str, prefix_lower: str) -> bool:
    """The check `scan` made, repeated on a fresh read. Judged on the text as
    `scan` decoded it (undecodable bytes dropped), so a byte `scan` never saw
    cannot make the two disagree and leave the note skipped forever."""
    seen = line.encode("utf-8", "surrogateescape").decode("utf-8", "ignore").rstrip("\r\n")
    return prefix_lower in seen.lower() and _is_strippable_line(seen)


def _replace_atomically(path: Path, text: str) -> bool:
    """Write *text* to *path* through a unique sibling temp file and `os.replace`,
    so a concurrent reader sees the old note or the new one, never a truncated
    one. Bytes round-trip (`newline=""` keeps CRLF/LF, `surrogateescape` keeps
    undecodable bytes) and the mode is copied over. False, note untouched, when
    the swap fails, e.g. a Windows sharing violation."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".leak-purge.tmp")
    tmp = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


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
            print(f"  notes skipped        : {summary['notes_skipped']} (changed since the scan, unreadable, or locked; left untouched)")
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
