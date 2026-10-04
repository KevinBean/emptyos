"""Agent resolver — loads named room agents for think() routing.

Apps call self.think(prompt, agent="blender-expert") and this module
resolves the agent definition (from data/apps/rooms/agents/<id>.json),
loads its knowledge files, and returns the enriched context for the LLM call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
        return self.kernel.config.data_dir / "apps" / "rooms" / "agents"

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

    def load_knowledge_chunks(self, agent: dict) -> list[dict]:
        """Same sources as load_knowledge, but returned as chunked items
        suitable for embedding-based retrieval.

        Each chunk: {id, source, text}. We chunk by H2 section when possible,
        else by char-length blocks of ~1500 chars. The chunk `id` is stable
        within an agent (file basename + chunk index) so embeddings are
        cache-friendly across reloads.

        Knowledge content is read in full (we ignore knowledge_char_limit
        here — the caller's retrieval pass picks top-K, so per-file truncation
        would just hide content from search rather than save tokens).
        """
        notes_path = self.kernel.config.notes_path or Path(".")
        items: list[dict] = []

        def _add_file(path: Path, source: str) -> None:
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                return
            for i, chunk_text in enumerate(self._chunk_for_retrieval(content)):
                items.append({
                    "id": f"{source}#{i}",
                    "source": source,
                    "text": chunk_text,
                })

        for rel in agent.get("knowledge_files", []):
            p = notes_path / rel
            if p.exists():
                _add_file(p, Path(rel).name)

        knowledge_dir = agent.get("knowledge_dir", "")
        if knowledge_dir:
            kdir = Path(knowledge_dir)
            if not kdir.is_absolute():
                kdir = self.kernel.config.data_dir / knowledge_dir
            if kdir.exists():
                for md_file in sorted(kdir.glob("*.md")):
                    _add_file(md_file, md_file.name)

        return items

    @staticmethod
    def _chunk_for_retrieval(content: str, target_chars: int = 1500) -> list[str]:
        """Split content into retrieval-sized chunks. Prefer H2 boundaries;
        merge tiny adjacent sections; split oversized ones by paragraph."""
        # First: split on H2 (^## ).
        sections: list[str] = []
        current: list[str] = []
        for line in content.split("\n"):
            if line.startswith("## ") and current:
                sections.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current).strip())
        sections = [s for s in sections if s]

        # Merge runs that are both small enough to fit together.
        merged: list[str] = []
        buf = ""
        for s in sections:
            if not buf:
                buf = s
                continue
            if len(buf) + len(s) + 2 <= target_chars:
                buf = buf + "\n\n" + s
            else:
                merged.append(buf)
                buf = s
        if buf:
            merged.append(buf)

        # Split oversized chunks by paragraph.
        out: list[str] = []
        for s in merged:
            if len(s) <= target_chars * 1.5:
                out.append(s)
                continue
            paras = s.split("\n\n")
            buf2 = ""
            for p in paras:
                if not buf2:
                    buf2 = p
                elif len(buf2) + len(p) + 2 <= target_chars:
                    buf2 = buf2 + "\n\n" + p
                else:
                    out.append(buf2)
                    buf2 = p
            if buf2:
                out.append(buf2)
        return out

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
