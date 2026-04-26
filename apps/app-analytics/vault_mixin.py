"""Vault Analytics mixin — vault health and statistics.

Absorbed from the standalone vault-analytics app. Provides vault file counts,
PARA distribution, recent/largest/stale file listings, uncovered folder scan.
"""

from __future__ import annotations

import time
from pathlib import Path


# vault_config defaults — resolved at runtime via app.vault_config(key, default)
PARA_DEFAULTS = {
    "inbox_dir": ("00_Inbox", "Inbox"),
    "projects_dir": ("10_Projects", "Projects"),
    "areas_dir": ("20_Areas", "Areas"),
    "resources_dir": ("30_Resources", "Resources"),
    "archive_dir": ("40_Archive", "Archive"),
    "journal_dir": ("50_Journal", "Journal"),
    "attachments_dir": ("99_Attachments", "Attachments"),
}


class VaultAnalyticsMixin:
    """Mixin that adds vault health analytics to AppAnalyticsApp.

    All capability calls go through self.app (the host BaseApp instance).
    """

    def __init__(self, app):
        self.app = app

    def _vault(self) -> Path:
        return self.app.vault_root

    def _para_folders(self) -> dict[str, str]:
        """Resolve PARA folder names via vault_config."""
        return {
            self.app.vault_config(key, default): label
            for key, (default, label) in PARA_DEFAULTS.items()
        }

    async def stats(self) -> dict:
        vault = self._vault()
        if not vault.exists():
            return {"error": "vault not found"}

        total_md = 0
        total_size = 0
        para = {}

        for folder_name, label in self._para_folders().items():
            folder = vault / folder_name
            if folder.exists():
                count = 0
                size = 0
                for f in folder.rglob("*.md"):
                    if f.is_file():
                        count += 1
                        size += f.stat().st_size
                para[label] = {"count": count, "size_mb": round(size / 1024 / 1024, 1)}
                total_md += count
                total_size += size

        # Other files not in PARA
        all_md = sum(1 for _ in vault.rglob("*.md"))
        para_md = sum(p["count"] for p in para.values())

        return {
            "vault_path": str(vault),
            "total_files": all_md,
            "total_size_mb": round(total_size / 1024 / 1024, 1),
            "para": para,
            "other_files": all_md - para_md,
        }

    async def scan_uncovered(self) -> dict:
        """Find vault folders not served by any app."""
        vault = self._vault()
        if not vault.exists():
            return {"uncovered": []}

        folders = set()
        skip = {".obsidian", ".git", ".trash", "99_Attachments", ".claude"}
        for d in vault.iterdir():
            if d.is_dir() and d.name not in skip and not d.name.startswith("."):
                folders.add(d.name)
                for sub in d.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        folders.add(f"{d.name}/{sub.name}")

        covered = set()
        try:
            vm = self.app.kernel.services.get("vault_map")
            if vm:
                for app_id, paths in vm.all().items():
                    for key, val in paths.items():
                        parts = str(val).split("/")
                        if parts:
                            covered.add(parts[0])
                        if len(parts) > 1:
                            covered.add(f"{parts[0]}/{parts[1]}")
        except Exception:
            pass

        covered.update(self._para_folders().keys())

        uncovered = sorted(folders - covered)
        result = []
        for folder in uncovered:
            path = vault / folder
            if path.exists():
                md_count = sum(1 for _ in path.rglob("*.md"))
                if md_count >= 3:
                    result.append({"folder": folder, "files": md_count})

        return {"uncovered": sorted(result, key=lambda x: -x["files"])}

    async def get_vault_summary(self) -> dict:
        """Summary for staff agent observation."""
        s = await self.stats()
        uncovered = await self.scan_uncovered()
        s["uncovered_folders"] = uncovered["uncovered"][:5]
        return s

    async def recent(self, limit: int = 20) -> list:
        """Most recently modified vault files."""
        vault = self._vault()
        if not vault.exists():
            return []
        files = []
        for f in vault.rglob("*.md"):
            if ".obsidian" in str(f) or ".git" in str(f):
                continue
            try:
                stat = f.stat()
                files.append({
                    "path": str(f.relative_to(vault)),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except Exception:
                continue
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files[:limit]

    async def largest(self, limit: int = 20) -> list:
        """Largest vault files."""
        vault = self._vault()
        if not vault.exists():
            return []
        files = []
        for f in vault.rglob("*.md"):
            if ".obsidian" in str(f) or ".git" in str(f):
                continue
            try:
                size = f.stat().st_size
                files.append({"path": str(f.relative_to(vault)), "size_kb": round(size / 1024, 1)})
            except Exception:
                continue
        files.sort(key=lambda x: x["size_kb"], reverse=True)
        return files[:limit]

    async def stale(self, days: int = 90, limit: int = 30) -> list:
        """Files not modified in N+ days."""
        vault = self._vault()
        if not vault.exists():
            return []
        cutoff = time.time() - days * 86400
        stale = []
        for f in vault.rglob("*.md"):
            if ".obsidian" in str(f) or ".git" in str(f):
                continue
            try:
                mtime = f.stat().st_mtime
                if mtime < cutoff:
                    days_old = int((time.time() - mtime) / 86400)
                    stale.append({"path": str(f.relative_to(vault)), "days_stale": days_old})
            except Exception:
                continue
        stale.sort(key=lambda x: x["days_stale"], reverse=True)
        return stale[:limit]

    async def growth(self) -> dict:
        """File count by PARA folder — for tracking vault growth."""
        vault = self._vault()
        result = {}
        for folder_name, label in self._para_folders().items():
            folder = vault / folder_name
            if folder.exists():
                result[label] = sum(1 for _ in folder.rglob("*.md"))
        return result
