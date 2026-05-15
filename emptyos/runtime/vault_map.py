"""Vault Map — discovers, persists, and auto-heals app data locations in the vault.

The vault map is a TOML file stored in the vault itself:
  {vault}/30_Resources/EmptyOS/_vault-map.toml

Features:
- Auto-generates on first boot by scanning vault patterns
- Fallback chain: settings override → map file → smart scan → defaults
- Smart detection: validates paths on access, auto-heals broken paths
- Vault watcher integration: re-scans on folder renames/moves
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("vault-map")

# Default paths + fallback alternatives for each key
# First entry is preferred, rest are fallbacks tried in order
DEFAULT_PATHS = {
    "journal": {
        "entries": ["50_Journal/{year}/{date}.md", "Journal/{year}/{date}.md", "Daily/{date}.md"],
        "weekly": ["50_Journal/{year}/{year}-W{week}.md"],
    },
    "task": {
        "scan_folders": ["00_Inbox,20_Areas", "Inbox,Areas"],
    },
    "contacts": {
        "people_dir": ["30_Resources/People", "People", "Contacts", "20_Areas/People"],
        "me_file": ["20_Areas/Personal-Info/_me.md"],
        "work_style": ["20_Areas/Personal-Dev/Work-Style-Assessment.md"],
        "mbti": ["20_Areas/Personal-Dev/MBTI - INFJ.md"],
    },
    "expense": {
        "log_dir": ["20_Areas/Finances", "Finances", "20_Areas/Finance"],
        "pattern": ["expense-log-*.md"],
    },
    "media": {
        "books": ["30_Resources/Books", "Books", "Reading"],
        "entertainment": ["30_Resources/Entertainment", "Entertainment", "Movies"],
    },
    "dictionary": {
        "words_dir": [
            "30_Resources/Reference/Dictionary",
            "30_Resources/Learning/Dictionary",
            "Dictionary",
            "Vocabulary",
        ],
    },
    "music": {
        "songs_dir": ["10_Projects/YouTube-Music-Channel/songs", "Music/songs", "Songs"],
    },
    "places": {
        "places_dir": ["30_Resources/Places", "Places"],
    },
    "items": {
        "items_file": ["20_Areas/Personal-Info/items.json", "Items/items.json"],
    },
    "healing": {
        "mood_log": ["20_Areas/Health/mood-log.md", "Health/mood-log.md"],
    },
    "projects": {
        "projects_dir": ["10_Projects", "Projects"],
        "archive_dir": ["40_Archive/10_Projects", "40_Archive/Projects", "Archive/Projects"],
    },
    "tracker": {
        "career": [
            "20_Areas/Career/Career-Development-Plan.md",
            "Career/Career-Development-Plan.md",
        ],
        "net_worth": ["20_Areas/Finances/net-worth/_净值跟踪.md"],
        "investment": ["20_Areas/Finances/Investment-Plan.md"],
    },
    "english": {
        "vocabulary": ["30_Resources/Learning/saved-vocabulary.md"],
        "practice_log": ["20_Areas/Speaking-Practice/practice-log.md"],
    },
    "timeline": {
        "career": ["20_Areas/Career/Career-Development-Plan.md"],
    },
    "interview-studio": {
        "jobs_dir": ["20_Areas/Career/Job-Applications", "Career/Job-Applications"],
    },
    "interview-briefing": {
        "jobs_dir": ["20_Areas/Career/Job-Applications", "Career/Job-Applications"],
    },
    "jobs": {
        "jobs_dir": ["20_Areas/Career/Job-Applications", "Career/Job-Applications"],
    },
    "quick-action": {
        "inbox": ["00_Inbox/_captures.md", "Inbox/_captures.md"],
    },
    "assistant": {
        "attachments": ["00_Inbox/_attachments", "Inbox/_attachments"],
    },
    "canvas": {
        "boards_dir": ["10_Projects/canvas", "Canvas"],
    },
    "lyrics": {
        "songs_dir": ["10_Projects/YouTube-Music-Channel/songs", "Music/songs", "Songs"],
    },
    "mv-creator": {
        "songs_dir": ["10_Projects/YouTube-Music-Channel/songs", "Music/songs", "Songs"],
    },
    "compose": {
        "songs_dir": ["10_Projects/YouTube-Music-Channel/songs", "Music/songs", "Songs"],
    },
    "model-bench": {
        "areas_dir": ["20_Areas", "Areas"],
    },
    "briefing": {
        "nutrition_log": ["20_Areas/Health/nutrition-log.md"],
    },
    "review": {},
    "staff": {
        "research_dir": ["30_Resources/Research", "Research"],
        "people_dir": ["30_Resources/People", "People"],
    },
    "hub": {
        "net_worth": ["20_Areas/Finances/net-worth/_净值跟踪.md"],
    },
    # net-worth retired — absorbed into finance app
    "publish": {
        "source_folder": ["30_Resources/Published", "Published", "Blog"],
        "scan_folders": ["30_Resources,20_Areas,10_Projects"],
        "podcast_dir": ["30_Resources/EmptyOS/podcast"],
    },
    "improv": {
        "sessions_dir": ["30_Resources/EmptyOS/improv/sessions"],
    },
    "earthing": {
        "projects": ["30_Resources/EmptyOS/earthing/{project_id}/{project_id}.md"],
        "soundings": ["30_Resources/EmptyOS/earthing/{project_id}/_soundings.json"],
        "geometry": ["30_Resources/EmptyOS/earthing/{project_id}/_geometry.json"],
        "scenarios_dir": ["30_Resources/EmptyOS/earthing/{project_id}/scenarios"],
    },
    "geo-cad": {
        "layers": ["30_Resources/EmptyOS/geo-cad"],
        "exports_dir": ["30_Resources/EmptyOS/geo-cad/_exports"],
        "imports_dir": ["30_Resources/EmptyOS/geo-cad/_imports"],
    },
    "actions": {
        "workflows_dir": ["30_Resources/EmptyOS/workflows"],
    },
    "forge": {
        "vault_dir": ["30_Resources/EmptyOS/forge"],
    },
}


class VaultMap:
    """Manages the vault-map.toml file and provides path lookups with fallback."""

    def __init__(self, vault_path: Path | None):
        self._vault = vault_path
        self._map: dict[str, dict[str, str]] = {}
        self._loaded = False
        self._dirty = False

    @property
    def map_path(self) -> Path | None:
        if not self._vault:
            return None
        return self._vault / "30_Resources" / "EmptyOS" / "_vault-map.toml"

    def load(self):
        """Load vault map from file, or auto-generate if missing."""
        if self._loaded:
            return
        self._loaded = True

        p = self.map_path
        if p and p.exists():
            self._map = self._parse_toml(p.read_text(encoding="utf-8"))
        elif self._vault and self._vault.exists():
            self._map = self._auto_generate()
            self._save()

    def get(self, app_id: str, key: str, default: str = "") -> str:
        """Get a vault path with fallback chain:
        1. Map file value (if path still exists)
        2. Smart scan (find alternative if configured path is broken)
        3. Default paths (try each fallback)
        4. Caller's default
        """
        self.load()

        # 1. Check map — validate path still exists
        app_section = self._map.get(app_id, {})
        val = app_section.get(key, "")
        if val and self._path_valid(val):
            return val

        # 2. Smart scan — configured path broken, find alternative
        if val and not self._path_valid(val):
            logger.info(
                "[VaultMap] Path broken for %s.%s: %s — scanning alternatives", app_id, key, val
            )
            found = self._find_alternative(app_id, key)
            if found:
                logger.info("[VaultMap] Auto-healed %s.%s → %s", app_id, key, found)
                self._update(app_id, key, found)
                return found

        # 3. Default paths — try each fallback
        fallbacks = DEFAULT_PATHS.get(app_id, {}).get(key, [])
        for fb in fallbacks:
            if self._path_valid(fb):
                self._update(app_id, key, fb)
                return fb

        # 4. First default (even if not found — template paths like {year} can't be validated)
        if fallbacks:
            first = fallbacks[0]
            if "{" in first:  # Template path — can't validate, trust it
                self._update(app_id, key, first)
                return first

        return default

    def get_absolute(self, app_id: str, key: str, default: str = "") -> Path | None:
        """Get an absolute path (vault_root / relative_path)."""
        rel = self.get(app_id, key, default)
        if not rel or not self._vault:
            return None
        return self._vault / rel

    def set(self, app_id: str, key: str, value: str):
        """Manually set a vault path and save."""
        self.load()
        self._update(app_id, key, value)

    def all(self) -> dict[str, dict[str, str]]:
        """Return the full map."""
        self.load()
        return dict(self._map)

    def rescan(self) -> dict[str, list[str]]:
        """Full rescan — validate all paths, heal broken ones. Returns changes."""
        self.load()
        changes = {}
        for app_id in list(self._map.keys()):
            for key, val in list(self._map[app_id].items()):
                if val and not self._path_valid(val):
                    found = self._find_alternative(app_id, key)
                    if found and found != val:
                        old = val
                        self._update(app_id, key, found)
                        changes.setdefault(app_id, []).append(f"{key}: {old} → {found}")
                    elif not found:
                        changes.setdefault(app_id, []).append(
                            f"{key}: {val} (BROKEN, no alternative)"
                        )

        # Also check for new apps with defaults but no map entry
        for app_id, paths in DEFAULT_PATHS.items():
            if app_id not in self._map:
                detected = {}
                for key, fallbacks in paths.items():
                    for fb in fallbacks:
                        if self._path_valid(fb):
                            detected[key] = fb
                            break
                if detected:
                    self._map[app_id] = detected
                    self._dirty = True
                    changes[app_id] = [f"NEW: {k}={v}" for k, v in detected.items()]

        if self._dirty:
            self._save()
        return changes

    # --- Internal ---

    def _path_valid(self, rel_path: str) -> bool:
        """Check if a relative vault path exists (handles patterns and templates)."""
        if not self._vault or not rel_path:
            return False
        if "{" in rel_path:
            return True  # Template — can't validate, trust it
        if "," in rel_path:
            # Comma-separated folders — at least one must exist
            return any((self._vault / p.strip()).exists() for p in rel_path.split(","))
        if "*" in rel_path:
            # Glob pattern — check parent dir
            parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
            return bool(parent) and (self._vault / parent).exists()
        return (self._vault / rel_path).exists()

    def _find_alternative(self, app_id: str, key: str) -> str:
        """Try to find an alternative path from fallback list."""
        fallbacks = DEFAULT_PATHS.get(app_id, {}).get(key, [])
        for fb in fallbacks:
            if self._path_valid(fb):
                return fb
        return ""

    def _update(self, app_id: str, key: str, value: str):
        """Update map + mark dirty."""
        if app_id not in self._map:
            self._map[app_id] = {}
        if self._map[app_id].get(key) != value:
            self._map[app_id][key] = value
            self._dirty = True
            self._save()

    def _auto_generate(self) -> dict[str, dict[str, str]]:
        """Scan vault and generate map from detected patterns."""
        result = {}
        if not self._vault:
            return result

        for app_id, paths in DEFAULT_PATHS.items():
            detected = {}
            for key, fallbacks in paths.items():
                for fb in fallbacks:
                    if self._path_valid(fb):
                        detected[key] = fb
                        break
            if detected:
                result[app_id] = detected

        return result

    def _save(self):
        """Write map to TOML file in vault."""
        p = self.map_path
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "# EmptyOS Vault Map",
            "# Auto-generated + auto-healed. User-editable.",
            "# Apps read paths via self.vault_config('key').",
            "# Paths are relative to vault root.",
            "",
        ]

        for app_id in sorted(self._map.keys()):
            section = self._map[app_id]
            if not section:
                continue
            lines.append(f"[{app_id}]")
            for k, v in sorted(section.items()):
                lines.append(f'{k} = "{v}"')
            lines.append("")

        p.write_text("\n".join(lines), encoding="utf-8")
        self._dirty = False

    def _parse_toml(self, content: str) -> dict[str, dict[str, str]]:
        """Simple TOML parser for vault map (flat sections only)."""
        result = {}
        current_section = ""
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                result[current_section] = {}
            elif "=" in line and current_section:
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                result[current_section][key.strip()] = val
        return result
