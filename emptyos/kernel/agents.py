"""Agent resolver — loads System and User GPT agents for think() routing.

Apps call self.think(prompt, agent="blender-expert") and this module
resolves the agent definition, loads its knowledge files, and returns
the enriched context for the LLM call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel import Kernel

# Cache TTL in seconds
_AGENT_CACHE_TTL = 60
_KNOWLEDGE_CACHE_TTL = 300


class AgentResolver:
    """Resolves agent definitions and loads their knowledge."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._agent_cache: dict[str, tuple[float, dict]] = {}  # key -> (timestamp, data)
        self._knowledge_cache: dict[str, tuple[float, str]] = {}  # agent_id -> (timestamp, text)

    def _agents_dir(self) -> Path:
        return self.kernel.config.data_dir / "apps" / "gpts" / "agents"

    def invalidate(self, agent_id: str | None = None):
        """Clear cache. Called after agent CRUD operations."""
        if agent_id:
            self._agent_cache.pop(agent_id, None)
            self._knowledge_cache.pop(agent_id, None)
        else:
            self._agent_cache.clear()
            self._knowledge_cache.clear()

    def resolve(self, agent_id_or_name: str) -> dict | None:
        """Load an agent by ID or name (case-insensitive).

        Tries exact ID match first, then scans by name.
        Results are cached for 60 seconds.
        """
        now = time.monotonic()

        # Check cache
        cached = self._agent_cache.get(agent_id_or_name)
        if cached and (now - cached[0]) < _AGENT_CACHE_TTL:
            return cached[1]

        agent = self._resolve_uncached(agent_id_or_name)
        if agent:
            self._agent_cache[agent_id_or_name] = (now, agent)
        return agent

    def _resolve_uncached(self, agent_id_or_name: str) -> dict | None:
        agents_dir = self._agents_dir()
        if not agents_dir.exists():
            return None

        # Try exact ID match
        path = agents_dir / f"{agent_id_or_name}.json"
        if path.exists():
            return self._load_json(path)

        # Scan by name (case-insensitive)
        target = agent_id_or_name.lower()
        for p in agents_dir.glob("*.json"):
            agent = self._load_json(p)
            if agent and agent.get("name", "").lower() == target:
                return agent

        return None

    def load_knowledge(self, agent: dict) -> str:
        """Load all knowledge for an agent. Returns concatenated text.

        Results are cached for 5 minutes per agent ID.
        """
        agent_id = agent.get("id", "")
        now = time.monotonic()

        # Check cache
        if agent_id:
            cached = self._knowledge_cache.get(agent_id)
            if cached and (now - cached[0]) < _KNOWLEDGE_CACHE_TTL:
                return cached[1]

        text = self._load_knowledge_uncached(agent)

        if agent_id:
            self._knowledge_cache[agent_id] = (now, text)
        return text

    def _load_knowledge_uncached(self, agent: dict) -> str:
        parts = []
        char_limit = agent.get("knowledge_char_limit", 2000)
        notes_path = self.kernel.config.notes_path or Path(".")

        # Load individual knowledge_files (vault-relative paths)
        for rel in agent.get("knowledge_files", []):
            content = self._read_file(notes_path / rel, char_limit)
            if content:
                name = Path(rel).name
                parts.append(f"### {name}\n{content}")

        # Load all .md files from knowledge_dir
        knowledge_dir = agent.get("knowledge_dir", "")
        if knowledge_dir:
            kdir = Path(knowledge_dir)
            if not kdir.is_absolute():
                kdir = self.kernel.config.data_dir / knowledge_dir
            if kdir.exists():
                for md_file in sorted(kdir.glob("*.md")):
                    content = self._read_file(md_file, char_limit)
                    if content:
                        parts.append(f"### {md_file.stem}\n{content}")

        return "\n\n".join(parts)

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _read_file(path: Path, char_limit: int) -> str:
        try:
            content = path.read_text(encoding="utf-8")
            return content[:char_limit] if char_limit else content
        except Exception:
            return ""
