"""Migrate scroll persona vault notes -> GPTs agent JSON.

Reads `{vault}/30_Resources/EmptyOS/scroll/personas/<id>.md`, converts
each note's frontmatter + body into a GPTs agent record at
`data/apps/gpts/agents/<id>.json` with `tier: "persona"` and a `persona`
sub-dict carrying the scroll-specific fields. Deletes migrated notes.

One-shot. Re-running is a no-op for already-migrated personas (skipped
when target JSON exists).
"""

from __future__ import annotations

import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / "data" / "apps" / "gpts" / "agents"
PERSONA_FIELDS = (
    "voice", "draw_style", "topics", "cadence", "linked_person",
    "mood", "needs", "preferences", "rituals",
)
PERSONA_DEFAULTS = {
    "mood": "neutral",
    "needs": {"social": 0.5, "fun": 0.5, "energy": 0.7},
    "preferences": {},
    "rituals": [],
}


def vault_path() -> Path:
    cfg = REPO / "emptyos.toml"
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    return Path(data["notes"]["path"])


def parse_note(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = yaml.safe_load(text[3:end].strip()) or {}
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def build_agent(fm: dict, body: str) -> dict:
    pid = fm.get("id") or "scroll-unknown"
    persona = {**PERSONA_DEFAULTS}
    for key in PERSONA_FIELDS:
        if key in fm and fm[key] not in (None, "", []):
            persona[key] = fm[key]
    return {
        "id": pid,
        "name": fm.get("name", pid),
        "tier": "persona",
        "system_prompt": body.strip(),
        "knowledge_files": (
            [fm["linked_person"]] if fm.get("linked_person") else []
        ),
        "knowledge_char_limit": 4000,
        "model": "",
        "temperature": 0.7,
        "tools": [],
        "server_actions": {},
        "builtin": False,
        "persona": persona,
        "created": str(fm.get("created") or datetime.now(timezone.utc).isoformat()),
    }


def main() -> int:
    vault = vault_path()
    src = vault / "30_Resources" / "EmptyOS" / "scroll" / "personas"
    if not src.exists():
        print(f"No source dir at {src} — nothing to migrate.")
        return 0

    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    migrated = 0
    skipped = 0
    for note in sorted(src.glob("*.md")):
        fm, body = parse_note(note.read_text(encoding="utf-8"))
        agent = build_agent(fm, body)
        target = AGENTS_DIR / f"{agent['id']}.json"
        if target.exists():
            print(f"  skip   {agent['id']} (already migrated)")
            skipped += 1
            continue
        target.write_text(
            json.dumps(agent, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        note.unlink()
        print(f"  +      {agent['id']} -> {target.relative_to(REPO)}")
        migrated += 1

    leftover = list(src.glob("*.md"))
    if not leftover:
        try:
            src.rmdir()
            print(f"  rmdir  {src.relative_to(vault)}")
        except OSError:
            pass

    print(f"\nMigrated {migrated}, skipped {skipped}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
