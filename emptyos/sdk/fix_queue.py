"""Fix-prompt queue — shared on-disk contract for the test-fix-verify loop.

Friction sources (``apps/dogfood-agent`` persona runs, ``apps/trace-miner``
syslog mining) write fix-prompts that ``apps/fix-agent`` reads and verifies.
This module is the single source of truth for that contract: the queue
directory, the filename slug, the frontmatter + ``## What the persona
reported`` block that fix-agent's ``_parse_prompt_meta`` parses, and the
queue-index / move-to-done lifecycle.

Keeping the writers in sync with the reader is the whole point — a silent
divergence here breaks cross-app verify. New friction sources should build
their prompt via ``render_frontmatter`` + ``persona_scenario_line`` +
``friction_block`` (or the ``BaseApp.surface_friction`` wrapper) rather than
restating the format. ``apps/dogfood-agent/friction.py`` predates this module
and migrates when next touched (CLAUDE.md vault-migration convention).

See ``.claude/rules/test-fix-verify-loop.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

# fix-agent reads prompts from this dir (via dogfood-agent's api_queue glob).
FIX_PROMPTS_REL = "apps/dogfood-agent/fix-prompts"
# Heading whose `> ...` quote fix-agent's _parse_prompt_meta extracts as
# friction_text. Changing this string is a cross-app breaking change.
FRICTION_HEADING = "## What the persona reported"
# Heading carrying the optional source-location hint (apps/.../file:LINE) from
# the platform locator. Lives in the BODY (not just frontmatter) so it survives
# fix-agent's strip_frontmatter and reaches the claude-cli fixer.
WHERE_HEADING = "## Where to look"
_KIND_ORDER = {"bug": 0, "confusing": 1, "missing": 2}


def slug_for_key(key: str) -> str:
    """Friction keys contain '::' and arbitrary chars — reduce to a path-safe
    .md filename. Deterministic so duplicate friction updates the same file."""
    slug = re.sub(r"[^a-z0-9]+", "-", (key or "").lower()).strip("-")
    return (slug[:80] or "friction") + ".md"


def render_frontmatter(fields: dict) -> str:
    """Render an ordered ``--- k: v ---`` block. Values are stringified as-is;
    callers pass lists pre-rendered (e.g. ``"['trace-miner']"``) to match the
    historical dogfood format the parser tolerates."""
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def persona_scenario_line(persona: str, scenario: str) -> str:
    """The ``**Persona**: X · **Scenario**: Y`` line fix-agent regexes for."""
    return f"**Persona**: {persona} · **Scenario**: {scenario}"


def friction_block(text: str, turn=None) -> str:
    """The canonical friction quote block. The ``> {text}`` line is what
    fix-agent's verify path reads back as the friction to retest."""
    head = FRICTION_HEADING + (f" (turn {turn})" if turn not in (None, "") else "")
    return f"{head}\n> {text}"


def where_to_look_block(source_hint: str) -> str:
    """Optional ``## Where to look`` body section pointing at a source location
    (e.g. ``apps/foo/pages/index.html:142``) surfaced by the platform locator.
    Returns ``""`` when there's no hint. The line is a best-effort pointer (the
    file is stable, the line may drift), not a patch coordinate."""
    hint = (source_hint or "").strip()
    if not hint:
        return ""
    return f"{WHERE_HEADING}\n`{hint}` (best-effort line — verify before editing)"


class FixPromptQueue:
    """Directory + index + lifecycle for the shared fix-prompt queue."""

    def __init__(self, data_dir):
        self._root = Path(data_dir) / FIX_PROMPTS_REL

    @property
    def dir(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "done").mkdir(exist_ok=True)
        return self._root

    def slug(self, key: str) -> str:
        return slug_for_key(key)

    def write(self, filename: str, content: str) -> Path:
        p = self.dir / filename
        p.write_text(content, encoding="utf-8")
        return p

    def move_to_done(self, filename: str) -> bool:
        src = self.dir / filename
        if not src.exists():
            return False
        try:
            src.rename(self.dir / "done" / filename)
            return True
        except Exception:
            return False

    def rebuild_index(self) -> None:
        """Maintain ``_queue.md`` — one file listing all pending items in
        priority order (bug > confusing > missing, then by recency). Format
        matches dogfood-agent's historical index so either writer is safe."""
        d = self.dir
        items = []
        for p in d.glob("*.md"):
            if p.name.startswith("_"):
                continue
            try:
                head = p.read_text(encoding="utf-8").splitlines()[:20]
                meta = {"kind": "?", "app": "", "last_seen": "", "count": 1}
                for line in head:
                    for key in ("kind", "app", "last_seen", "count"):
                        if line.startswith(key + ":"):
                            val = line.split(":", 1)[1].strip()
                            meta[key] = int(val) if key == "count" and val.isdigit() else val
                items.append({"file": p.name, **meta})
            except Exception:
                continue
        items.sort(key=lambda x: x["last_seen"], reverse=True)
        items.sort(key=lambda x: _KIND_ORDER.get(x["kind"], 9))
        lines = [
            "# Dogfood fix-prompt queue",
            "",
            f"_{len(items)} pending — auto-generated. Process with: read a file → fix → mv to `done/`._",
            "",
        ]
        if not items:
            lines.append("(empty — nothing to fix right now)")
        else:
            for it in items:
                app_tag = f" · app `{it['app']}`" if it.get("app") else ""
                lines.append(
                    f"- **{it['kind']}**{app_tag} · `{it['file']}` · seen {it['count']}× · last {it['last_seen'] or '?'}"
                )
        (d / "_queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
