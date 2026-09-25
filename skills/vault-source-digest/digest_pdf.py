#!/usr/bin/env python3
"""digest_pdf.py — deterministic half of the `source-digest` skill.

Convert a reference PDF (engineering standard / textbook / paper) into the
page-marked full-text artifact the EmptyOS KB cites, with two reliability
guarantees the ad-hoc workflow lacked:

  1. PII / watermark HARD GATE — known publisher stamps (IEEE Xplore, ENA
     delivery watermarks) are stripped, then the assembled text is re-scanned
     for residue (emails, "supplied/licensed ... to:" lines, optional
     .eos-personal patterns). Any residue => exit 3, write NOTHING. The skill
     must not proceed past a non-zero exit.
  2. TOC COVERAGE LEDGER — the document's table of contents is flattened to
     an ID-keyed table (level <= 2, with page numbers, a keep/skip-default
     hint, and a Status column) so coverage is driven by the real structure,
     not by whatever sections happened to get sampled. Unlike a scratch
     checklist, this ledger is written into the vault next to the full text
     and is DURABLE — it is written once (never silently overwritten) and
     doubles as resumable "what's left" state across sessions/sub-agents.
     The judgment half updates its Status column via kb_coverage_status.py
     as clause notes get written (see SKILL.md "Resuming a partial digest").

This script writes ONLY the full-text artifact + the coverage ledger. It
never writes clause/reference notes — that is the skill's judgment half.

Usage
-----
    python digest_pdf.py <pdf> --slug IEEE_1547_2018 \
        --title "IEEE Std 1547-2018 — Interconnection and Interoperability of DER ..." \
        --copyright "(c) 2018 IEEE. All rights reserved." \
        [--standard "IEEE Std 1547-2018"] \
        [--strip-extra "regex" ...] [--force] [--regen-coverage] [--vault auto]

Exit codes: 0 ok | 2 usage/extraction error | 3 PII residue (nothing written)
            | 4 existing full-text found under some layout (reconcile; nothing written).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

# ── full-text artifact format (must match existing _fulltext/*.md) ───────────
FULLTEXT_SUBDIR = "30_Resources/EmptyOS/kb/sources/_fulltext"
HEADER_ATTRIB = (
    "> Extracted from PDF via automated conversion (PyMuPDF). "
    "Verbatim source text for KB citation; personal study aid only."
)

# ── watermark / licensing stamps stripped BEFORE assembly ────────────────────
# Each is re.search-ed against the stripped physical line; a match drops the
# whole line. Tolerant of mid-line wrap (head + known continuation both listed).
BUILTIN_STRIP = [
    r"Authorized licensed use limited to:.*Restrictions apply\.?",  # IEEE full line
    r"^Authorized licensed use limited to:",                        # IEEE head if wrapped
    r"Downloaded on .+ from IEEE Xplore",                           # IEEE variant
    r"^Supplied by .+ to:.*",                                       # ENA-style delivery watermark
    r"^or distribution is prohibited\.$",                           # ENA wrapped continuation
]

# ── PII residue detectors (run AFTER strip; any hit => exit 3) ───────────────
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RESIDUE_RE = [
    EMAIL_RE,
    re.compile(r"\bSupplied by .+ to:", re.I),
    re.compile(r"\blicensed (use )?.*\bto:\s*\S", re.I),
]

# TOC titles that are boilerplate (coverage map flags these skip-by-default).
BOILERPLATE = re.compile(
    r"\b(contents|foreword|scope|normative reference|references|definition|"
    r"acronym|terms|abbreviation|bibliography|acknowledg|participant|"
    r"introduction|front cover|title page|back cover|figure|table of)\b",
    re.I,
)
CLAUSE_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")

# A PDF's embedded bookmark outline is sometimes present but useless as a TOC
# (e.g. CIGRE TB 669's outline is just 2 "Page vierge" blank-page bookmarks).
# Below this many entries, extract_toc()'s result is treated as unreliable
# and the Contents-page parser is tried too — see main().
MIN_RELIABLE_TOC_ENTRIES = 5


def err(msg: str, code: int = 2):
    print(f"digest_pdf: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_vault(arg: str) -> Path:
    if arg and arg != "auto":
        p = Path(arg)
        if not p.is_dir():
            err(f"--vault path not a directory: {arg}")
        return p
    import os

    if os.environ.get("EOS_VAULT"):
        return Path(os.environ["EOS_VAULT"])
    # vault-connection.json (written by the external-vault-connector)
    for vc in (
        _repo_root() / ".claude" / "vault-connection.json",
        Path.cwd() / ".claude" / "vault-connection.json",
    ):
        try:
            vp = json.loads(vc.read_text(encoding="utf-8")).get("vault_path")
            if vp and Path(vp).is_dir():
                return Path(vp)
        except Exception:
            pass
    # emptyos.toml notes.path
    for tomlp in (_repo_root() / "emptyos.toml", Path.cwd() / "emptyos.toml"):
        try:
            import tomllib

            vp = tomllib.load(open(tomlp, "rb")).get("notes", {}).get("path")
            if vp and Path(vp).is_dir():
                return Path(vp)
        except Exception:
            pass
    err("could not resolve vault path; pass --vault <dir>")


def existing_fulltexts(vault: Path, slug: str) -> list[Path]:
    """Find a full-text already filed for this standard, under EITHER layout.

    Prior digests have used two conventions: the canonical
    ``_fulltext/<SLUG>.md`` (cited via ``source_file:``) and a legacy
    per-standard subdir ``<note-slug>/<note-slug>-fulltext.md`` (cited via
    ``local_text:``). Detecting both prevents writing a second copy when an
    earlier digest chose the other layout — the duplicate-fulltext trap.
    """
    src = vault / "30_Resources/EmptyOS/kb/sources"
    note_slug = slug.lower().replace("_", "-")
    found: list[Path] = []
    for p in (src / "_fulltext" / f"{slug}.md", src / "_fulltext" / f"{note_slug}.md"):
        if p.exists() and p not in found:
            found.append(p)
    if src.is_dir():
        for p in src.rglob("*.md"):
            if p in found:
                continue
            name = p.name.lower()
            rel = str(p).replace("\\", "/").lower()
            if ("fulltext" in name or "full-text" in name) and note_slug in rel:
                found.append(p)
    return found


def _repo_root() -> Path:
    """The EmptyOS repo root when this script ships inside it.

    The bundled copy lives at `<repo>/skills/vault-source-digest/`, so two
    parents up is the root. The per-machine copy under ~/.claude/skills has
    no repo above it — every caller treats a miss as "not found" and falls
    through to a cwd-relative lookup, so a wrong answer here degrades to the
    same path as no answer.
    """
    return Path(__file__).resolve().parents[2]


def load_eos_personal_patterns() -> list:
    """Best-effort: enrich the PII detector with the repo's .eos-personal.

    The bundled copy of this script ships inside the repo, so `_repo_root()`
    resolves; the per-machine copy under ~/.claude/skills does not, and the
    built-in detectors always run regardless.
    """
    repo = _repo_root()
    pp_file = repo / "emptyos" / "sdk" / "personal_patterns.py"
    patterns_file = repo / ".eos-personal"
    if not (pp_file.exists() and patterns_file.exists()):
        return []
    try:
        spec = importlib.util.spec_from_file_location("personal_patterns", pp_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.load(str(patterns_file), on_error=lambda line, e: None)
    except Exception:
        return []


def extract_pages(pdf: Path) -> list[str]:
    """Return per-page text. PyMuPDF primary, pypdf fallback."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf))
        pages = [doc[i].get_text() for i in range(doc.page_count)]
        doc.close()
        return pages
    except ImportError:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        return [(pg.extract_text() or "") for pg in reader.pages]
    except ImportError:
        err("neither PyMuPDF (fitz) nor pypdf is importable")


def extract_toc(pdf: Path) -> list[tuple[int, str, int]]:
    """(level, title, page) entries; [] if no embedded TOC.

    Note: a *non-empty* return is not automatically trustworthy — some PDFs
    carry an embedded bookmark outline that's junk relative to the real
    Contents page (e.g. CIGRE TB 669's outline is just two "Page vierge"
    (blank page) bookmarks, nothing else). Callers should treat a
    suspiciously sparse result as unreliable and fall back to
    parse_contents_pages() too — see the MIN_RELIABLE_TOC_ENTRIES gate in
    main().
    """
    try:
        import fitz

        doc = fitz.open(str(pdf))
        toc = doc.get_toc() or []
        doc.close()
        return toc
    except Exception:
        return []


# Contents-page line: "<title> ......... <page>"  (dot-leader + trailing page no.)
_DOTLEADER = re.compile(r"^(.*?\S)\s*\.{2,}\s*(\d+)\s*$")
# Fallback leader for documents whose Contents page uses plain whitespace
# padding instead of dots (seen in CIGRE TB 669 — some rows dot-lead, others
# don't, on the same page). Tried only as a second pass, and only when the
# dot-leader pass comes up thin — see parse_contents_pages().
_WSLEADER = re.compile(r"^(.*?\S)\s{2,}(\d{1,4})\s*$")

_BARE_NUM = re.compile(r"^\d+(?:\.\d+)*$")
# A "List of Figures"/"List of Tables" page uses the same whitespace-padded
# leader shape as a real Contents page (caption text + trailing page number),
# so _WSLEADER alone can't tell them apart structurally. Caption rows are
# reliably prefixed "Figure N"/"Table N" though — real section titles in
# these documents never are — so filter on that instead of trying to detect
# "this whole page is a figure list" (safer: false-negatives here just mean
# one stray row, not a whole missed Contents page).
_CAPTION_PREFIX = re.compile(r"^(Figure|Table)\s+\d+\b", re.I)


def _scan_leader(pages: list[str], leader_re: "re.Pattern[str]",
                  min_hits_per_page: int = 3) -> list[tuple[int, str, int]]:
    """Scan pages[:15] for `leader_re` lines and reconstruct (level, title,
    page), with a bare-clause-number-precedes-title re-attach pass. Shared by
    both the dot-leader and whitespace-leader passes in
    parse_contents_pages() — same body, parameterized leader pattern.

    Two sanity guards keep a loose leader (like _WSLEADER) from picking up
    stray wrapped body text as a spurious TOC row: the page number must be a
    plausible 1-9999 value, and the title must be at least 3 chars.
    """
    out = []
    for txt in pages[:15]:
        lines = [ln.strip() for ln in txt.split("\n")]
        if sum(1 for ln in lines if leader_re.match(ln)) < min_hits_per_page:
            continue  # not a contents page for this leader style
        pending = ""  # a bare clause-number line often precedes its title line
        for ln in lines:
            if _BARE_NUM.match(ln):
                pending = ln
                continue
            m = leader_re.match(ln)
            if not m:
                if ln:
                    pending = ""
                continue
            title, page_s = m.group(1).strip(), m.group(2)
            page = int(page_s)
            if not (1 <= page <= 9999) or len(title) < 3 or _CAPTION_PREFIX.match(title):
                pending = ""
                continue
            if not CLAUSE_PREFIX.match(title) and pending:
                title = f"{pending} {title}"  # re-attach split number
            pending = ""
            cm = CLAUSE_PREFIX.match(title)
            level = (cm.group(1).count(".") + 1) if cm else 1
            out.append((level, title, page))
    return out


def parse_contents_pages(pages: list[str]) -> list[tuple[int, str, int]]:
    """Fallback when get_toc() is empty: scan early 'Contents' pages for
    dot-leader lines and reconstruct (level, title, page). Level is inferred
    from the leading clause-number depth (e.g. 4.3.1 -> level 3).

    Dot-leader is tried first (the common case). If it finds too few rows —
    some documents mix dot-leader and plain-whitespace-leader rows on the
    same Contents page, or use no dots at all — a second pass with a looser
    whitespace-only leader is tried. Kept as two explicit passes rather than
    one combined pattern so each stays legible and the false-positive risk
    of the looser pattern is easy to reason about.
    """
    rows = _scan_leader(pages, _DOTLEADER)
    if len(rows) < 5:
        rows = _scan_leader(pages, _WSLEADER) or rows
    return rows


def strip_watermarks(pages: list[str], extra: list[str]) -> list[str]:
    pats = [re.compile(p, re.I) for p in (BUILTIN_STRIP + (extra or []))]
    out = []
    for txt in pages:
        kept = []
        for ln in txt.split("\n"):
            s = ln.strip()
            if s and any(p.search(s) for p in pats):
                continue
            kept.append(ln.rstrip())
        out.append("\n".join(kept))
    return out


def scan_residue(body: str) -> list[tuple[int, str]]:
    detectors = list(RESIDUE_RE) + load_eos_personal_patterns()
    hits = []
    for i, ln in enumerate(body.split("\n"), 1):
        for d in detectors:
            if d.search(ln):
                hits.append((i, ln.strip()[:120]))
                break
    return hits


def build_fulltext(slug: str, title: str, copyright_: str, pages: list[str]) -> str:
    n = len(pages)
    head = title or slug
    lines = [f"# {head}", "", HEADER_ATTRIB,
             f"> {copyright_ or '(c) the publisher.'} Do not redistribute.", ""]
    for i, txt in enumerate(pages, 1):
        lines += ["---", "", f"<!-- Page {i} of {n} -->", "", txt.rstrip()]
    return "\n".join(lines).rstrip() + "\n"


def _cell(s: str) -> str:
    """Escape a value for use inside a markdown table cell."""
    return (s or "").replace("|", "\\|")


def build_coverage(slug: str, standard: str, npages: int,
                   toc: list[tuple[int, str, int]]) -> str:
    """Build the coverage LEDGER — an ID-keyed markdown table, not a scratch
    checklist. Every row starts Status=pending; the judgment half (a Claude
    session, or kb_coverage_status.py on its behalf) flips rows to
    written:<slug> / skipped:<reason> / folded-into:<slug> as notes get
    written. The ledger is durable (written into the vault, never silently
    overwritten — see main()'s no-clobber guard) so "what's left" survives
    across sessions and is race-safe across parallel sub-agents, as long as
    ONLY the orchestrating session edits Status (sub-agents just report back
    which IDs they finished).
    """
    rows, n_top, n_sub, next_id = [], 0, 0, 1
    if toc:
        for lvl, ttl, page in toc:
            if lvl > 2:
                continue
            ttl = (ttl or "").strip()
            m = CLAUSE_PREFIX.match(ttl)
            clause = m.group(1) if m else ""
            boiler = BOILERPLATE.search(ttl) is not None or not clause
            hint = "skip?" if boiler else "keep"
            if not boiler:
                n_sub += 1
                if lvl == 1:
                    n_top += 1
            rows.append(
                f"| {next_id} | {_cell(clause) or '—'} | {_cell(ttl)} | "
                f"{page} | {hint} | pending |"
            )
            next_id += 1
    if n_top:
        upper = n_top + round((n_sub - n_top) * 0.4)
        band = (f"~{n_top}–{upper} clause notes — one per major (level-1) clause, "
                f"expanding to notable sub-clauses that carry distinct rules")
    elif n_sub:
        band = f"~{n_sub} clause notes (no level-1 split detected)"
    else:
        band = "TOC not embedded — scan the Contents page(s) manually"
    out = [
        f"# Coverage ledger — {standard or slug}",
        "",
        f"- Pages: {npages}",
        f"- Substantive clauses: {n_top} level-1, {n_sub} level-≤2",
        f"- Suggested coverage: **{band}**. Default to a note for EVERY section "
        f"that carries content — including definitions and informative annexes "
        f"(they often hold real methods/worked examples). Give worked examples "
        f"`kind: case`. Only *skip* pure legal/navigation front-matter (cover, "
        f"title, participants, contents, disclaimers); only *fold* genuine "
        f"duplicates. The `skip?` hints below flag boilerplate candidates — "
        f"they are NOT a default to skip.",
        "",
        "NOTE: this is about which sections get a distilled *clause note*. The "
        "full verbatim text already contains every page/section — nothing is "
        "ever omitted from it.",
        "",
        "Status legend: `pending` · `written:<slug>` · `skipped:<reason>` · "
        "`folded-into:<slug>`. The orchestrating session owns Status updates "
        "(via kb_coverage_status.py) — sub-agents report back which IDs they "
        "finished; they never edit this file directly. Decide keep / fold / "
        "skip for EVERY row; log skips with a reason.",
        "",
    ]
    if rows:
        out.append("| ID | Clause | Title | Page | Hint | Status |")
        out.append("|----|--------|-------|------|------|--------|")
        out += rows
    else:
        out.append(
            "- (no embedded TOC — open the full-text Contents pages and "
            "hand-build a table in this same `ID | Clause | Title | Page | "
            "Hint | Status` format at this same path; the rest of the "
            "workflow treats it identically either way)"
        )
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Digest a reference PDF into KB full-text + coverage map.")
    ap.add_argument("pdf", help="path to the source PDF")
    ap.add_argument("--slug", required=True, help="artifact slug, e.g. IEEE_1547_2018")
    ap.add_argument("--title", default="", help="H1 title for the full-text artifact")
    ap.add_argument("--copyright", dest="copyright_", default="", help="copyright line")
    ap.add_argument("--standard", default="", help="standard id for the coverage map header")
    ap.add_argument("--strip-extra", nargs="*", default=[], help="extra watermark regexes")
    ap.add_argument("--vault", default="auto", help="vault root, or 'auto'")
    ap.add_argument("--coverage-out", default="",
                     help="coverage ledger path (default: vault _fulltext/<slug>.coverage.md, "
                          "next to the full text)")
    ap.add_argument("--force", action="store_true", help="overwrite existing full-text")
    ap.add_argument("--regen-coverage", action="store_true",
                     help="reset the coverage ledger to a fresh all-pending table, even if one "
                          "already exists (DESTROYS any in-progress Status — use only when "
                          "intentionally re-deriving the TOC, not mid-digest)")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        err(f"PDF not found: {pdf}")

    vault = resolve_vault(args.vault)
    out_dir = vault / FULLTEXT_SUBDIR
    out_path = out_dir / f"{args.slug}.md"

    # Reconcile FIRST — refuse to write a second full-text copy when one already
    # exists under EITHER the canonical _fulltext/ layout or a legacy subdir.
    existing = existing_fulltexts(vault, args.slug)
    if existing and not args.force:
        print(f"\n{'='*60}\n  EXISTING FULL-TEXT FOUND — NOTHING WRITTEN\n{'='*60}")
        for p in existing:
            layout = "canonical" if "_fulltext" in str(p).replace("\\", "/") else "legacy subdir"
            print(f"  [{layout}] {p}")
        print("\nA full text for this standard is already filed. REUSE it as the")
        print("`source_file:` (or `local_text:`) target for new clause notes instead")
        print("of duplicating it. Only re-extract if it is missing or incomplete.")
        print("To replace/migrate to the canonical _fulltext/<SLUG>.md, pass --force,")
        print("then delete the old copy and repoint any notes that cited it.")
        sys.exit(4)

    pages = extract_pages(pdf)
    if not any(p.strip() for p in pages):
        err("no extractable text (scanned/image PDF?) — OCR not supported")
    pages = strip_watermarks(pages, args.strip_extra)
    body = build_fulltext(args.slug, args.title, args.copyright_, pages)

    # HARD GATE — residue means write nothing.
    hits = scan_residue(body)
    if hits:
        print(f"\n{'='*60}\n  PII / WATERMARK RESIDUE — {len(hits)} line(s); NOTHING WRITTEN\n{'='*60}")
        for ln, prev in hits[:30]:
            print(f"  L{ln}: {prev}")
        print("\nAdd a --strip-extra regex for the offending stamp and re-run.")
        sys.exit(3)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Coverage ledger — durable, vault-resident by default (next to the full
    # text), NOT a scratch CWD file. Written ONCE: an existing ledger is never
    # silently clobbered (it may carry in-progress Status from a partial
    # digest) unless --regen-coverage is passed explicitly.
    cov_path = Path(args.coverage_out) if args.coverage_out else out_dir / f"{args.slug}.coverage.md"
    if cov_path.exists() and not args.regen_coverage:
        print(f"\n{'='*60}\n  LEDGER ALREADY EXISTS — NOT OVERWRITTEN\n{'='*60}")
        print(f"  {cov_path}")
        print("\nIt may carry in-progress Status from a partial digest. Read it and")
        print("filter for `pending` rows to resume, or pass --regen-coverage to force")
        print("a fresh all-pending table (this DESTROYS any recorded Status).")
        cov_written = False
    else:
        toc = extract_toc(pdf)
        if len(toc) < MIN_RELIABLE_TOC_ENTRIES:
            # Embedded outline is empty OR too sparse to trust (e.g. a
            # blank-page-only bookmark list) — try the Contents-page parser
            # too, preferring it when it actually finds something.
            toc = parse_contents_pages(pages) or toc
        cov = build_coverage(args.slug, args.standard, len(pages), toc)
        cov_path.write_text(cov, encoding="utf-8")
        cov_written = True

    out_path.write_text(body, encoding="utf-8")

    print(f"OK  full-text -> {out_path}  ({len(pages)} pages, {len(body.splitlines())} lines)")
    if cov_written:
        print(f"OK  ledger    -> {cov_path}")
        print()
        print(cov)


if __name__ == "__main__":
    main()
