"""Vault Analytics — vault health and statistics.

Scans the vault for file counts, folder sizes, PARA distribution.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


# vault_config defaults — resolved at runtime via self.vault_config(key, default)
PARA_DEFAULTS = {
    "inbox_dir": ("00_Inbox", "Inbox"),  # default
    "projects_dir": ("10_Projects", "Projects"),  # default
    "areas_dir": ("20_Areas", "Areas"),  # default
    "resources_dir": ("30_Resources", "Resources"),  # default
    "archive_dir": ("40_Archive", "Archive"),  # default
    "journal_dir": ("50_Journal", "Journal"),  # default
    "attachments_dir": ("99_Attachments", "Attachments"),  # default
}


class VaultAnalyticsApp(BaseApp):

    def _vault(self) -> Path:
        return self.kernel.config.notes_path or Path(".")

    def _para_folders(self) -> dict[str, str]:
        """Resolve PARA folder names via vault_config."""
        return {
            self.vault_config(key, default): label
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

        # Collect top-level + second-level folders
        folders = set()
        skip = {".obsidian", ".git", ".trash", "99_Attachments", ".claude"}
        for d in vault.iterdir():
            if d.is_dir() and d.name not in skip and not d.name.startswith("."):
                folders.add(d.name)
                for sub in d.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        folders.add(f"{d.name}/{sub.name}")

        # Get paths registered in vault map
        covered = set()
        try:
            vm = self.kernel.services.get("vault_map")
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

        # Also cover PARA folders
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

    async def get_summary(self) -> dict:
        """Summary for staff agent observation."""
        s = await self.stats()
        uncovered = await self.scan_uncovered()
        s["uncovered_folders"] = uncovered["uncovered"][:5]
        return s

    @web_route("GET", "/api/uncovered")
    async def api_uncovered(self, request):
        return await self.scan_uncovered()

    @cli_command("vault", help="Vault health and statistics")
    async def cmd_vault(self, action: str = "stats"):
        s = await self.stats()
        if "error" in s:
            print(f"  {s['error']}")
            return
        print(f"\n  Vault: {s['vault_path']}")
        print(f"  Total: {s['total_files']} files, {s['total_size_mb']} MB\n")
        for label, data in s["para"].items():
            bar = "#" * min(int(data["count"] / 50), 30)
            print(f"    {label:<14} {data['count']:>5} files  {data['size_mb']:>5.1f} MB  {bar}")
        if s["other_files"]:
            print(f"    {'Other':<14} {s['other_files']:>5} files")
        print()

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        return await self.stats()

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        """Most recently modified vault files."""
        import os
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
        limit = int(request.query_params.get("limit", "20"))
        return files[:limit]

    @web_route("GET", "/api/largest")
    async def api_largest(self, request):
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
        limit = int(request.query_params.get("limit", "20"))
        return files[:limit]

    @web_route("GET", "/api/stale")
    async def api_stale(self, request):
        """Files not modified in 90+ days."""
        import time
        vault = self._vault()
        if not vault.exists():
            return []
        threshold = int(request.query_params.get("days", "90"))
        cutoff = time.time() - threshold * 86400
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
        limit = int(request.query_params.get("limit", "30"))
        return stale[:limit]

    @web_route("GET", "/api/growth")
    async def api_growth(self, request):
        """File count by PARA folder — for tracking vault growth."""
        vault = self._vault()
        growth = {}
        for folder_name, label in self._para_folders().items():
            folder = vault / folder_name
            if folder.exists():
                growth[label] = sum(1 for _ in folder.rglob("*.md"))
        return growth
