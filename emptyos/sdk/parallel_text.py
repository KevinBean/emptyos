"""Parallel-text parser — turn a markdown note into left/right corresponding rows.

A *parallel-text note* pairs two columns in markdown ``| left | right |``
tables, grouped under ``##`` section headers, optionally with fenced code
blocks (used verbatim, e.g. a mantra heart + romanization). It is the data
shape behind the shared ``EOS_UI.parallelText`` frontend component
(``emptyos/web/static/eos-parallel.js``).

Reusable across: 原文|注音 (chanting), 原文|译文 (translation), bilingual
song lyrics, interlinear glosses, KB clause|注释 — any "corresponding text".

Pure functions only — no I/O, no kernel. Apps read the note (e.g. via
``BaseApp.vault_read_at``) and pass the text here.

    from emptyos.sdk.parallel_text import parse_parallel_md
    data = parse_parallel_md(note_text)
    # -> {"title": str, "sections": [{"title", "rows": [{left, right}], "blocks": [str]}]}
"""
from __future__ import annotations

import re

_ROW = re.compile(r"^\|(.+?)\|(.+?)\|\s*$")
_SEP = set("-: |")
# header-row labels to skip (so the column header isn't rendered as a pair)
_HEADER_LEFT = {"原文", "原句", "原", "句", "left", "source"}
_HEADER_RIGHT = {"注音", "拼音", "念诵注音", "译文", "翻译", "注", "译", "right", "gloss", "target"}


def parse_parallel_md(text: str) -> dict:
    """Parse parallel-text markdown into ``{title, sections}``.

    - ``# H1`` (first) → ``title``
    - ``## H2`` → a section
    - ``| a | b |`` rows → ``{"left": a, "right": b}`` (separator + header rows dropped)
    - fenced ```` ``` ```` blocks → ``section["blocks"]`` (verbatim)

    Empty sections (no rows and no blocks) are dropped.
    """
    title = ""
    sections: list[dict] = []
    cur: dict | None = None
    in_fence = False
    fence_buf: list[str] = []

    for raw in (text or "").splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_fence:
                if cur is not None and fence_buf:
                    cur["blocks"].append("\n".join(fence_buf))
                fence_buf = []
                in_fence = False
            else:
                in_fence = True
                fence_buf = []
            continue
        if in_fence:
            fence_buf.append(raw)
            continue

        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue

        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "rows": [], "blocks": []}
            sections.append(cur)
            continue

        m = _ROW.match(line)
        if m and cur is not None:
            a = m.group(1).strip()
            b = m.group(2).strip()
            if not a or set(a) <= _SEP:  # |---|---| separator
                continue
            if a in _HEADER_LEFT and b in _HEADER_RIGHT:  # column header
                continue
            cur["rows"].append({"left": a, "right": b})

    sections = [s for s in sections if s["rows"] or s["blocks"]]
    return {"title": title, "sections": sections}


def section_label(title: str) -> str:
    """Strip 【…】 / （…） annotation noise for menus/headers (display only)."""
    t = re.sub(r"【[^】]*】", "", title)
    t = re.sub(r"（[^）]*）", "", t)
    t = re.sub(r"\([^)]*\)", "", t)
    return t.strip() or title
