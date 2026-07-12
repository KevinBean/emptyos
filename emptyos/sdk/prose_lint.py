"""Prose tone linter — deterministic AI-tell + banned-pattern scanner for published writing.

The doctrine lives in the vault voice guide (``30_Resources/Published/_voice.md``);
this module is its mechanical half: pure functions that count the *measurable*
tells so a draft can be checked before human review. Judgment stays human —
findings are advisory and nothing is ever auto-rewritten (audits.md posture:
heuristics fire on healthy prose too; thresholds below are tuned against the
voice guide's own calibration sentences, which must lint clean).

Two rule families:

* **banned** (severity ``high``) — the voice guide's banned words & patterns:
  hype vocabulary, press-release openers, engagement bait, business-blog filler.
* **ai-tell** (severity ``medium``/``low``) — structural fingerprints of
  LLM-drafted prose: the "isn't X. It's Y." pivot repeated past a density
  threshold, stacked "That's the …" openers, triadic anaphora, AI filler
  phrases, and an em-dash rate far beyond the house style's own fondness.

Pure stdlib, no kernel imports — safe to import from a one-shot script
(`python -c` / scripts/check_prose_tone.py) per daemon-handling rule 4.
Consumers: ``scripts/check_prose_tone.py`` (CLI); writing-editor / publish
surfaces can call ``lint_prose`` directly when they want a tone chip.
"""

from __future__ import annotations

import re
from typing import Iterator

# ── Thresholds (per document unless noted) ──────────────────────────────────
# The "isn't X. It's Y." pivot is fine once or twice; past this rate it reads
# machine-made. Rate is per 1000 words with a floor of 2 occurrences.
NOT_X_ITS_Y_PER_1000 = 2.0
THATS_THE_MAX = 2          # sentence-initial "That's the …" openers allowed
# (calibrated against the published model-or-harness post, which uses 2 and reads fine)
EM_DASH_PER_100_MAX = 2.5  # house style *likes* em-dashes; only flag excess

# ── banned — from the voice guide, verbatim intent ──────────────────────────
_BANNED: list[tuple[str, str, str]] = [
    ("hype", r"\brevolutionar(?:y|ies|ize[sd]?)\b", "hype vocabulary"),
    ("hype", r"\bgame[- ]chang(?:ing|er[s]?)\b", "hype vocabulary"),
    ("hype", r"\bsupercharg(?:e[sd]?|ing)\b", "hype vocabulary"),
    ("hype", r"\b10x\b", "hype vocabulary"),
    # "unlock" is hype only in front of an abstract noun ("unlock your potential").
    # Literal unlocking — a session lock, a device, a door — is ordinary technical
    # prose, so the old bare-stem match fired on every healthy sentence about a
    # lock. Precision over recall here: a missed hype "unlock" costs a word; a
    # false high-severity hit on a correct post teaches the author to ignore the gate.
    ("hype",
     r"\bunlock(?:s|ed|ing)?\s+"
     r"(?:(?:your|our|its|the|a|new|more|real|true|full|hidden|untapped)\s+)*"
     r"(?:potential|value|growth|power|possibilit(?:y|ies)|insight[s]?|"
     r"opportunit(?:y|ies)|productivity|capabilit(?:y|ies)|revenue|roi|"
     r"success|efficienc(?:y|ies)|synerg(?:y|ies))\b",
     "hype vocabulary ('unlock <abstract noun>'); literal unlocking is fine"),
    ("hype", r"\bdelv(?:e[sd]?|ing)\b", "hype vocabulary"),
    ("hype", r"\bleverag(?:e[sd]?|ing)\b",
     "banned as a verb; even as a noun, prefer a plainer word"),
    ("hype", r"\bjourney\b", "hype vocabulary (unless a literal journey)"),
    ("hype", r"\blandscape\b", "hype vocabulary (unless literal terrain)"),
    ("opener", r"\b(?:excited|thrilled|proud)\s+to\s+(?:announce|share)\b",
     "press-release opener"),
    ("opener", r"\bbig news\b", "press-release opener"),
    ("bait", r"\blet that sink in\b", "engagement bait"),
    ("bait", r"\bhere's the kicker\b", "engagement bait"),
    ("bait", r"\bfollow for more\b", "engagement bait"),
    ("filler", r"\bin today's fast-paced world\b", "business-blog filler"),
    ("filler", r"\bthe future of \w+ is here\b", "business-blog filler"),
    ("filler", r"\bas we all know\b", "business-blog filler"),
]

# ── ai-tell phrase list — LLM filler that survives casual editing ───────────
_AI_FILLER: list[tuple[str, str]] = [
    (r"\bit'?s worth noting\b", "AI filler"),
    (r"\bneedless to say\b", "AI filler"),
    (r"\bin conclusion\b", "AI filler"),
    (r"\bin essence\b", "AI filler"),
    (r"\bnot only\b[^.!?\n]{0,60}\bbut also\b", "the 'not only … but also' frame"),
    (r"(?:^|[.!?]\s+)(?:Moreover|Furthermore|Additionally|Ultimately|Crucially|Importantly),",
     "sentence-initial connective filler"),
    (r"\bdeep dive\b", "AI filler"),
    (r"\bdive into\b", "AI filler"),
]

# The two-sentence pivot: "… isn't X. It's Y." (also — variant inside one
# sentence). The single most reliable structural tell when it stacks up.
_NOT_X_ITS_Y = re.compile(
    r"\b(?:isn't|is not|aren't|are not|wasn't|weren't)\b[^.!?\n]{0,80}[.!?][\"'”’]?\s+"
    r"(?:It's|It is|They're|They are|That's)\s",
    re.IGNORECASE,
)
_NOT_X_DASH_ITS_Y = re.compile(
    r"\b(?:isn't|is not|aren't|are not)\b[^.!?\n]{0,60}—\s*(?:it's|they're)\b",
    re.IGNORECASE,
)
_THATS_THE = re.compile(r"(?:^|[.!?]\s+)That's the \w+", re.MULTILINE)

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_CODE_FENCE = re.compile(r"^(```|~~~)", re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z']+")

# First words too common to count as deliberate anaphora.
_ANAPHORA_STOP = {"the", "a", "an", "i", "it", "we", "you", "this", "and"}


def strip_markdown(text: str) -> list[tuple[int, str]]:
    """Reduce a markdown document to prose lines, preserving line numbers.

    Returns ``[(lineno, prose), …]`` (1-based line numbers into the original).
    Drops YAML frontmatter, fenced code blocks, image lines (captions are
    checked by eye — their technical alt-text is FP fuel), and markdown
    scaffolding (heading hashes, blockquote markers, inline code spans,
    link URLs — link text survives).
    """
    body = text
    fm_offset = 0
    m = _FRONTMATTER.match(text)
    if m:
        fm_offset = m.group(0).count("\n")
        body = text[m.end():]

    out: list[tuple[int, str]] = []
    in_fence = False
    for i, raw in enumerate(body.splitlines(), start=fm_offset + 1):
        line = raw.rstrip()
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.lstrip().startswith("!["):
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"`[^`]*`", " ", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = line.replace("**", "").replace("*", "")
        if line.strip():
            out.append((i, line))
    return out


def _joined(lines: list[tuple[int, str]]) -> tuple[str, list[tuple[int, int]]]:
    """Join prose lines into one string plus a char-offset → lineno map."""
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []  # (start_offset, lineno)
    pos = 0
    for lineno, line in lines:
        offsets.append((pos, lineno))
        parts.append(line)
        pos += len(line) + 1
    return "\n".join(parts), offsets


def _line_at(offsets: list[tuple[int, int]], pos: int) -> int:
    lineno = offsets[0][1] if offsets else 1
    for start, ln in offsets:
        if start > pos:
            break
        lineno = ln
    return lineno


def _sentences(text: str) -> Iterator[str]:
    for s in _SENTENCE_SPLIT.split(text.replace("\n", " ")):
        s = s.strip()
        if s:
            yield s


def _broken_image_embeds(text: str) -> list[tuple[int, str]]:
    """Find ``![alt](url)`` embeds whose alt contains a ``]`` before ``](``.

    Markdown closes the alt at the first ``]`` — a bracket inside the caption
    (``[DO:]``, ``[BUTTON:...]``, cite-style ``[1]``) truncates the embed and
    the whole line renders as literal text. Vault ``![[wikilink]]`` embeds
    are legitimate and skipped, as are frontmatter and code fences.
    Returns ``[(lineno, excerpt), ...]``.
    """
    out: list[tuple[int, str]] = []
    body = text or ""
    offset = 0
    m = _FRONTMATTER.match(body)
    if m:
        offset = m.group(0).count("\n")
        body = body[m.end():]
    in_fence = False
    for i, line in enumerate(body.splitlines(), start=offset + 1):
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # inline code spans document syntax (`![image: <x>](url)` tables) —
        # blank them so only live embeds are scanned
        line = re.sub(r"`[^`]*`", "", line)
        pos = 0
        while True:
            start = line.find("![", pos)
            if start < 0:
                break
            pos = start + 2
            if line[pos:pos + 1] == "[":       # ![[wikilink]] embed — fine
                continue
            close = line.find("]", pos)
            if close < 0 or line[close:close + 2] != "](":
                out.append((i, line[start:start + 70]))
                break                            # one finding per line is enough
    return out


def lint_prose(text: str, *, source: str = "") -> dict:
    """Lint one markdown document. Returns ``{"source", "metrics", "findings"}``.

    ``findings`` rows carry ``rule`` / ``severity`` (high|medium|low) /
    ``line`` / ``excerpt`` / ``message``. Never raises on odd input; empty
    text returns empty findings.
    """
    lines = strip_markdown(text or "")
    joined, offsets = _joined(lines)
    words = len(_WORD.findall(joined))
    findings: list[dict] = []

    def add(rule: str, severity: str, pos: int, excerpt: str, message: str) -> None:
        findings.append({
            "rule": rule,
            "severity": severity,
            "line": _line_at(offsets, pos),
            "excerpt": excerpt.strip()[:90],
            "message": message,
        })

    # broken image embeds — a ']' inside the alt truncates ![alt](url) and the
    # line ships as literal text (checked on the raw document; strip_markdown
    # drops image lines, so this can't run on the stripped prose)
    for lineno, excerpt in _broken_image_embeds(text or ""):
        findings.append({
            "rule": "broken-image-embed",
            "severity": "high",
            "line": lineno,
            "excerpt": excerpt,
            "message": "alt text contains ']' before '](' — the embed renders as "
                       "literal text; remove square brackets from the caption",
        })

    # banned words & patterns — every hit is a finding
    for rule, pat, msg in _BANNED:
        for m in re.finditer(pat, joined, re.IGNORECASE):
            add(f"banned-{rule}", "high", m.start(), m.group(0), msg)

    # AI filler phrases — every hit
    for pat, msg in _AI_FILLER:
        for m in re.finditer(pat, joined, re.IGNORECASE):
            add("ai-filler", "medium", m.start(), m.group(0), msg)

    # "isn't X. It's Y." pivot — density-thresholded
    pivots = list(_NOT_X_ITS_Y.finditer(joined)) + list(_NOT_X_DASH_ITS_Y.finditer(joined))
    allowed = max(2.0, NOT_X_ITS_Y_PER_1000 * words / 1000.0)
    if len(pivots) > allowed:
        for m in pivots:
            add("not-x-its-y", "medium", m.start(), m.group(0),
                f"{len(pivots)} 'isn't X. It's Y.' pivots in {words} words "
                f"(allowed ≈ {allowed:.0f}) — vary the construction")

    # "That's the …" sentence openers — count-thresholded
    thats = list(_THATS_THE.finditer(joined))
    if len(thats) > THATS_THE_MAX:
        for m in thats:
            add("thats-the-opener", "low", m.start(), m.group(0).strip(),
                f"{len(thats)} sentences open with \"That's the …\" — keep at most {THATS_THE_MAX}")

    # triadic anaphora — 3+ consecutive sentences sharing their first two words
    sents = list(_sentences(joined))
    run_start = 0
    for idx in range(1, len(sents) + 1):
        def key(s: str) -> tuple[str, ...]:
            w = [w.lower() for w in _WORD.findall(s)[:2]]
            return tuple(w)
        if idx < len(sents) and key(sents[idx]) == key(sents[run_start]) and key(sents[run_start]):
            continue
        run_len = idx - run_start
        first = key(sents[run_start]) if run_start < len(sents) else ()
        if run_len >= 3 and first and first[0] not in _ANAPHORA_STOP:
            pos = joined.find(sents[run_start])
            add("anaphora-triple", "low", max(pos, 0), sents[run_start],
                f"{run_len} consecutive sentences open with '{' '.join(first)} …' — "
                "triadic anaphora reads machine-made")
        run_start = idx

    # em-dash rate — metric always; finding only past the excess threshold
    dashes = joined.count("—")
    dash_rate = (dashes * 100.0 / words) if words else 0.0
    if dash_rate > EM_DASH_PER_100_MAX and words >= 200:
        add("em-dash-rate", "low", 0, f"{dashes} em-dashes",
            f"{dash_rate:.1f} em-dashes per 100 words (house ceiling "
            f"{EM_DASH_PER_100_MAX}) — convert some asides to plain sentences")

    findings.sort(key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f["severity"]], f["line"]))
    return {
        "source": source,
        "metrics": {
            "words": words,
            "sentences": len(sents),
            "em_dashes": dashes,
            "em_dash_per_100": round(dash_rate, 2),
            "not_x_its_y": len(pivots),
            "thats_the_openers": len(thats),
        },
        "findings": findings,
    }
