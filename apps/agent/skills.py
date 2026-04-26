"""Skill discovery — Claude-Code-compatible SKILL.md loader.

Skills are markdown playbooks stored as `<skills_dir>/<name>/SKILL.md` with
YAML frontmatter (`name`, `description`). Three discovery roots are scanned
in precedence order — later wins on name collision:

    1. bundled — `<repo>/skills/`              (ships with EmptyOS)
    2. project — `<repo>/.claude/skills/`      (repo-scoped overrides)
    3. user    — `~/.claude/skills/`           (user's own global skills)

The agent uses progressive disclosure: names + descriptions go into the system
prompt; the full SKILL.md is loaded on demand via the `Skill` tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    source: str  # "bundled" | "project" | "user"
    params: list[str] = None  # declared param names from frontmatter, e.g. ["topic", "format"]

    def __post_init__(self):
        if self.params is None:
            self.params = []


def _skill_dirs(repo_root: Path) -> list[tuple[str, Path]]:
    return [
        ("bundled", repo_root / "skills"),
        ("project", repo_root / ".claude" / "skills"),
        ("user",    Path.home() / ".claude" / "skills"),
    ]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML frontmatter reader — only `key: value` pairs. Returns
    (frontmatter, body). Good enough for SKILL.md headers without pulling in
    PyYAML at tool-call time."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    body = text[end + 4:].lstrip("\n")
    return fm, body


def parse_skill_args(arg: str) -> dict[str, str]:
    """Parse skill invocation args into a substitution dict.

    Supports:
      positional   /skill word1 word2        → {"arg": "word1 word2", "1": "word1", "2": "word2"}
      named        /skill key=value          → {"arg": "key=value", "key": "value"}
      quoted       /skill key="multi word"   → {"key": "multi word"}
      mixed        /skill topic="a b" n=3    → {"topic": "a b", "n": "3", "arg": ...}

    Always includes "arg" = full raw arg string.
    """
    if not arg:
        return {"arg": ""}

    result: dict[str, str] = {"arg": arg}
    # Try named key=value parsing (with optional quoting)
    import re
    named_pattern = re.compile(r'(\w+)=(?:"([^"]*)"|(\'[^\']*\')|(\S+))')
    named_matches = list(named_pattern.finditer(arg))

    if named_matches:
        for m in named_matches:
            key = m.group(1)
            value = m.group(2) or (m.group(3) or "").strip("'") or m.group(4) or ""
            result[key] = value
    else:
        # Positional — split on whitespace
        parts = arg.split()
        for i, p in enumerate(parts, 1):
            result[str(i)] = p

    return result


def substitute_skill_params(body: str, params: dict[str, str]) -> str:
    """Replace {{key}} placeholders in skill body with values from params dict."""
    import re
    def replace(m):
        key = m.group(1).strip()
        return params.get(key, m.group(0))  # leave unreplaced if key not found
    return re.sub(r"\{\{(\w+)\}\}", replace, body)


def _first_meaningful_line(body: str) -> str:
    """Pluck the first non-empty, non-heading line — used as a description
    fallback when a SKILL.md lacks frontmatter."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Strip common markdown emphasis wrappers so the preview is clean.
        line = line.lstrip("*_").rstrip("*_").strip()
        if line:
            return line[:240]
    return ""


def discover_skills(repo_root: Path) -> dict[str, Skill]:
    """Build the skill catalog. User skills override project override bundled."""
    catalog: dict[str, Skill] = {}
    for source, root in _skill_dirs(repo_root):
        if not root.exists():
            continue
        for skill_dir in root.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, body = _parse_frontmatter(text)
            name = fm.get("name") or skill_dir.name
            desc = fm.get("description") or _first_meaningful_line(body)
            # params: comma-separated param names, e.g. "topic, format=bullets"
            raw_params = fm.get("params", "")
            params = [p.split("=")[0].strip() for p in raw_params.split(",") if p.strip()] if raw_params else []
            catalog[name] = Skill(name=name, description=desc, path=skill_md, source=source, params=params)
    return catalog
