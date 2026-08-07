#!/usr/bin/env python3
"""Compact one conversation-source archive down to its readable arc.

    python data/imports/t2-work/arc_extract.py <shortid-or-path>

The EMFieldCalc archives inline the whole calculator on every turn — 700 KB
across 8 turns is normal, and the 34-record cluster totals 7.1 MB. None of that
is the conversation; it is the same HTML pasted over and over. What decides a
digest is the *instructions* and the *retractions*, which live in the user turns
and in the assistant's prose.

Compression is very uneven, so check the output size before committing to a
record: `90cd00f7` went 219 KB -> 23 KB (11 %) because it was mostly pasted
HTML, while `e5ef7a98` went 82 KB -> 63 KB (77 %) because it is mostly prose.
A 63 KB arc is a session's reading on its own.

So: keep every user turn's prose in full (that is where "please list the SPLP
algorithm as not implemented" lived), keep the assistant's prose, and replace
each fenced block with a one-line stamp naming its language and size. Attachment
and document blocks the exporter inlines are stamped the same way.

Read-only with respect to the Vault. Output goes to
``data/imports/t2-work/arc-<shortid>.md`` per the skill's rule that a
vault-relative path is an API payload, never a local join.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from vault_paths import require_vault_root  # noqa: E402

ORIGINALS = require_vault_root() / "30_Resources/conversations/originals/claude"
OUT_DIR = REPO / "data/imports/t2-work"  # arcs are session data; only the code moved

TURN_RE = re.compile(r"^### (\d{3}) · (User|Claude) · (\S+)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")
# The exporter inlines attachments as pseudo-XML; they are bulk, not dialogue.
DOC_OPEN_RE = re.compile(r"^\s*<(document|attachment|source)\b", re.I)
DOC_CLOSE_RE = re.compile(r"^\s*</(document|attachment|source)>", re.I)


def resolve(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    hits = sorted(ORIGINALS.glob(f"*{arg}*.md"))
    if not hits:
        raise SystemExit(f"no archive matching {arg!r} under {ORIGINALS}")
    if len(hits) > 1:
        # A short id is unique; a slug fragment may not be. Refuse rather than guess.
        raise SystemExit(
            f"{arg!r} matches {len(hits)} archives:\n  "
            + "\n  ".join(h.name for h in hits)
        )
    return hits[0]


def compact(text: str) -> tuple[str, dict]:
    out: list[str] = []
    stats = {"turns": 0, "fenced": 0, "fenced_lines": 0, "doc_lines": 0}
    fence: str | None = None
    fence_lang = ""
    fence_len = 0
    in_doc = False
    doc_len = 0

    for line in text.splitlines():
        if in_doc:
            doc_len += 1
            if DOC_CLOSE_RE.match(line):
                out.append(f"    ⟦inlined document · {doc_len} lines⟧")
                stats["doc_lines"] += doc_len
                in_doc, doc_len = False, 0
            continue

        if fence is not None:
            # A closing fence must be at least as long as the opener.
            m = FENCE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                out.append(
                    f"    ⟦{fence_lang or 'code'} block · {fence_len} lines⟧"
                )
                stats["fenced"] += 1
                stats["fenced_lines"] += fence_len
                fence, fence_lang, fence_len = None, "", 0
            else:
                fence_len += 1
            continue

        m = TURN_RE.match(line)
        if m:
            stats["turns"] += 1
            out.append("")
            out.append(f"### [{m.group(1)}] {m.group(2)}  ·  {m.group(3)[:19]}")
            continue

        if DOC_OPEN_RE.match(line):
            in_doc, doc_len = True, 1
            continue

        m = FENCE_RE.match(line)
        if m:
            fence, fence_lang, fence_len = m.group(1), m.group(2), 0
            continue

        out.append(line)

    if fence is not None:  # unterminated fence — say so rather than silently eat it
        out.append(f"    ⟦{fence_lang or 'code'} block · {fence_len} lines · UNTERMINATED⟧")
        stats["fenced"] += 1
        stats["fenced_lines"] += fence_len

    # Collapse runs of blank lines the elisions leave behind.
    compacted: list[str] = []
    blanks = 0
    for line in out:
        if line.strip():
            blanks = 0
            compacted.append(line.rstrip())
        else:
            blanks += 1
            if blanks <= 1:
                compacted.append("")
    return "\n".join(compacted).strip() + "\n", stats


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = resolve(sys.argv[1])
    raw = src.read_text(encoding="utf-8", errors="replace")
    body, stats = compact(raw)

    m = re.search(r'source_conversation_id: "?([0-9a-f-]+)', raw)
    short = m.group(1)[:8] if m else src.stem[-8:]
    dest = OUT_DIR / f"arc-{short}.md"
    dest.write_text(body, encoding="utf-8")

    kb_in = len(raw) // 1024
    kb_out = len(body) // 1024
    print(f"{src.name}")
    print(
        f"  {kb_in} KB -> {kb_out} KB  ({100 * kb_out / max(kb_in, 1):.0f}%)  "
        f"turns={stats['turns']}  fenced={stats['fenced']} "
        f"({stats['fenced_lines']} lines)  doc_lines={stats['doc_lines']}"
    )
    print(f"  -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
