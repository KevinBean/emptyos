"""Journal markdown parsing — mood emoji map, entry regex, section extract/replace."""

from __future__ import annotations

import re

MOOD_EMOJI = {"great": "😊", "good": "🙂", "okay": "😐", "low": "😔", "bad": "😢"}
MOOD_SCORE = {"great": 5, "good": 4, "okay": 3, "low": 2, "bad": 1}
EMOJI_TO_MOOD = {v: k for k, v in MOOD_EMOJI.items()}
ENTRY_PATTERN = re.compile(r"^- \*\*(\d{2}:\d{2})\*\*\s+(\S+)\s+(.*)")


def parse_entries(content: str) -> list[dict]:
    """Parse journal entries from ### Journal section."""
    entries = []
    in_section = False
    for line in content.split("\n"):
        if line.strip() == "### Journal":
            in_section = True
            continue
        if in_section and line.startswith("### "):
            break
        if in_section:
            m = ENTRY_PATTERN.match(line.strip())
            if m:
                emoji = m.group(2)
                entries.append({
                    "time": m.group(1),
                    "mood": EMOJI_TO_MOOD.get(emoji, "okay"),
                    "emoji": emoji,
                    "text": m.group(3),
                })
    return entries


def extract_section(content: str, header: str) -> str:
    """Extract text under a specific ### header."""
    lines = content.split("\n")
    result = []
    in_section = False
    for line in lines:
        if line.strip() == header:
            in_section = True
            continue
        if in_section and line.startswith("### ") or (in_section and line.startswith("## ")):
            break
        if in_section:
            result.append(line)
    return "\n".join(result).strip()


def replace_section(content: str, header: str, new_text: str) -> str:
    """Replace text under a specific ### header."""
    lines = content.split("\n")
    result = []
    in_section = False
    replaced = False
    for line in lines:
        if line.strip() == header:
            result.append(line)
            result.append("")
            result.append(new_text)
            result.append("")
            in_section = True
            replaced = True
            continue
        if in_section and (line.startswith("### ") or line.startswith("## ")):
            in_section = False
        if not in_section:
            result.append(line)
    if not replaced:
        result.append(f"\n{header}\n\n{new_text}\n")
    return "\n".join(result)
