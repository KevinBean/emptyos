"""Publish — turn vault notes into a public static website.

Supports multiple site profiles (blog, docs, portfolio, etc.)
stored in sites.json. Each site has its own source folder, theme,
deploy target, and output directory.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import (
    parse_frontmatter, parse_llm_json, slugify, strip_frontmatter,
)

from . import chatbot as _chatbot
from . import media as _media
from .builder import SiteBuilder

_DEFAULT_SITE = {
    "id": "default",
    "name": "Default Site",
    "source_folder": "",  # resolved via vault_config at runtime
    "site_name": "My Site",
    "site_description": "",
    "author": "",
    "author_bio": "",
    "social_links": "",
    "theme": "void-dark",
    "domain": "",
    "repo": "",
    "languages": "",
    "original_language": "en",
    "favicon": "",  # filename inside source_folder (e.g. "favicon.svg")
    "search_engines": True,  # false → noindex meta + disallow-all robots.txt
    "analytics": {"enabled": False, "collector_url": ""},  # {enabled, collector_url (blank → inherit global)}
    "chatbot": {
        "enabled": False,
        "endpoint": "",                # e.g. "https://chat.binbian.net"
        "persona": "",                 # extra system-prompt text appended at service side
        "daily_cap_usd": 2.0,          # per-site daily $ ceiling enforced by the chat service
        "starter_questions": [],       # 3-4 chips shown on first widget open
        "model": "gpt-4.1-mini",
    },
}

_SITE_FIELDS = [
    "name", "source_folder", "site_name", "site_description", "author",
    "author_bio", "social_links", "theme", "domain", "repo",
    "languages", "original_language", "favicon", "search_engines",
    "analytics", "template", "chatbot",
]

from .prompts import (
    REVIEW_PROMPT_HEADER, CHAT_SYSTEM_PROMPT, APPLY_REVIEW_PROMPT,
    SUGGEST_TITLE_PROMPT, SUGGEST_TOPICS_PROMPT, WRITER_SYSTEM,
    POLISH_PROMPT, EXPAND_PROMPT, COMPRESS_PROMPT, TRANSLATE_PROMPT,
    OUTLINE_PROMPT,
)



class PublishApp(BaseApp):

    # ── Site profiles ──────────────────────────────────────────

    def _sites_path(self) -> Path:
        return self.data_dir / "sites.json"

    def _load_sites(self) -> list[dict]:
        p = self._sites_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return self._migrate_legacy_sites()

    def _save_sites(self, sites: list[dict]) -> None:
        self._sites_path().parent.mkdir(parents=True, exist_ok=True)
        self._sites_path().write_text(
            json.dumps(sites, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _migrate_legacy_sites(self) -> list[dict]:
        """Create initial sites.json from existing publish.* settings."""
        site = dict(_DEFAULT_SITE)
        svc = self.kernel.services.get_optional("settings")
        if svc:
            for field in _SITE_FIELDS:
                val = svc.get(f"publish.{field}", "")
                if val:
                    site[field] = val
            sf = svc.get("publish.source_folder", "")
            if sf:
                site["source_folder"] = sf
        if site["site_name"] and site["site_name"] != "My Site":
            site["name"] = site["site_name"]
        sites = [site]
        self._save_sites(sites)
        if svc:
            svc.set("publish.active_site", "default")
        return sites

    def _active_site_id(self) -> str:
        svc = self.kernel.services.get_optional("settings")
        if svc:
            return svc.get("publish.active_site", "default") or "default"
        return "default"

    def _active_site(self) -> dict:
        sites = self._load_sites()
        site_id = self._active_site_id()
        for s in sites:
            if s["id"] == site_id:
                return s
        return sites[0] if sites else dict(_DEFAULT_SITE)

    def _get_site(self, site_id: str) -> dict | None:
        for s in self._load_sites():
            if s["id"] == site_id:
                return s
        return None

    # ── Derived helpers (now site-aware) ───────────────────────


    def _vault_dir(self) -> str:
        # Use Config.notes_path (resolved absolute) so paths returned to the
        # writer round-trip cleanly through load-post — a relative
        # `notes.path` would otherwise cause load-post to re-prefix the vault
        # dir on top of an already-vault-rooted save path.
        p = self.kernel.config.notes_path
        return str(p) if p else ""

    def _source_folder(self, site: dict | None = None) -> str:
        s = site or self._active_site()
        folder = s.get("source_folder", "")
        if not folder:
            folder = self.vault_config("source_folder", "30_Resources/Published")
        return folder

    def _site_dir(self, site: dict | None = None) -> Path:
        s = site or self._active_site()
        return self.data_dir / "sites" / s["id"] / "site"

    def _site_config(self, site: dict | None = None) -> dict:
        s = site or self._active_site()
        return {k: s.get(k, "") for k in _SITE_FIELDS if k != "name"}

    def _analytics_script(self, site: dict) -> str:
        """Return the inline analytics beacon JS for this site, or empty string.

        Pulls the script from the web-analytics app if installed and enabled
        for this site. Called synchronously at build time.
        """
        analytics = site.get("analytics") or {}
        if not analytics.get("enabled"):
            return ""
        wa = self.kernel.apps.instances.get("web-analytics") if self.kernel.apps else None
        if wa is None or not hasattr(wa, "render_beacon"):
            return ""
        return wa.render_beacon(
            site=site.get("id", ""),
            collector=analytics.get("collector_url") or None,
        )

    def _cross_site_links(self, current: dict) -> list[dict]:
        """All other sites with a domain configured — for the cross-site footer."""
        out = []
        for s in self._load_sites():
            if s["id"] == current["id"]:
                continue
            domain = (s.get("domain") or "").strip()
            if not domain:
                continue
            out.append({
                "name": s.get("name") or s["id"],
                "url": f"https://{domain}",
            })
        return out

    def _builder(self, site: dict | None = None) -> SiteBuilder:
        s = site or self._active_site()
        config = self._site_config(s)
        config["analytics_script"] = self._analytics_script(s)
        config["cross_site_links"] = self._cross_site_links(s)
        # Inject site_id into the chatbot block so the widget meta tags can
        # reference it. The chat service uses this site_id as its config key.
        cb = dict(config.get("chatbot") or {})
        cb["site_id"] = s["id"]
        config["chatbot"] = cb
        return SiteBuilder(
            vault_dir=self._vault_dir(),
            source_folder=self._source_folder(s),
            output_dir=str(self._site_dir(s)),
            config=config,
        )

    def _state_path(self) -> Path:
        return self.data_dir / "publish_state.json"

    def _load_state(self) -> dict:
        p = self._state_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {}

    def _save_state(self, data: dict, site: dict | None = None) -> None:
        s = site or self._active_site()
        self._state_path().parent.mkdir(parents=True, exist_ok=True)
        existing = self._load_state()
        site_state = existing.get("sites", {})
        ss = site_state.get(s["id"], {})
        ss.update(data)
        site_state[s["id"]] = ss
        existing["sites"] = site_state
        self._state_path().write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )

    def _site_state(self, site: dict | None = None) -> dict:
        s = site or self._active_site()
        state = self._load_state()
        return state.get("sites", {}).get(s["id"], {})

    # --- Core methods ---

    def scan(self, site: dict | None = None, include_drafts: bool = False) -> list[dict]:
        """Scan vault for publishable notes (double-gate: folder + frontmatter)."""
        return self._builder(site).scan(include_drafts=include_drafts)

    def build(self, site: dict | None = None) -> dict:
        """Build the static site for a given (or active) site profile."""
        s = site or self._active_site()
        stats = self._builder(s).build()
        self._save_state({
            "last_build": datetime.now().isoformat(),
            "last_build_stats": stats,
        }, s)
        return stats

    async def deploy(self, site: dict | None = None) -> dict:
        """Deploy site to static hosting via git push."""
        s = site or self._active_site()
        site_dir = self._site_dir(s)
        if not site_dir.exists() or not (site_dir / "index.html").exists():
            return {"error": "No built site found. Run build first."}

        config = self._site_config(s)
        repo = config.get("repo", "")
        if not repo:
            return {"error": "No repo configured. Set repo in site settings."}

        remote_url = f"https://github.com/{repo}.git"

        git_dir = site_dir / ".git"
        if not git_dir.exists():
            await self._run_git(site_dir, "init")
            await self._run_git(site_dir, "checkout", "-b", "gh-pages")
            await self._run_git(site_dir, "remote", "add", "origin", remote_url)
        else:
            await self._run_git(site_dir, "remote", "set-url", "origin", remote_url)

        await self._run_git(site_dir, "add", "-A")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        out, err, code = await self._run_git(
            site_dir, "commit", "-m", f"publish: {timestamp}"
        )
        if code != 0 and "nothing to commit" in (out + err).lower():
            return {"status": "nothing_changed", "message": "Site already up to date."}

        out, err, code = await self._run_git(
            site_dir, "push", "-u", "origin", "gh-pages", "--force"
        )
        if code != 0:
            return {"error": f"Push failed: {err}"}

        self._save_state({
            "last_deploy": datetime.now().isoformat(),
            "deploy_repo": repo,
        }, s)

        await self.emit("publish:deployed", {
            "repo": repo, "time": timestamp, "site": s["id"],
        })

        domain = config.get("domain", "")
        url = f"https://{domain}" if domain else f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[-1]}"
        return {"status": "deployed", "url": url}

    async def _run_git(self, cwd: Path, *args: str) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )
        stdout, stderr = await proc.communicate()
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )

    # --- CLI ---

    @cli_command("publish", help="Publish vault notes as a static website")
    async def cmd_publish(self, action: str = "list", **kwargs):
        site_id = kwargs.get("site", "")
        site = self._get_site(site_id) if site_id else None

        if action == "sites":
            sites = self._load_sites()
            active = self._active_site_id()
            self.print_rich("[bold]Site profiles:[/bold]")
            for s in sites:
                marker = " [green]*[/green]" if s["id"] == active else ""
                self.print_rich(f"  {s['id']}{marker}  [dim]{s.get('site_name', '')}[/dim]  src={s.get('source_folder', '')}")
            return

        if action == "list":
            posts = self.scan(site)
            if not posts:
                self.print_rich("[dim]No publishable notes found.[/dim]")
                self.print_rich(f"[dim]Source: {self._source_folder(site)}[/dim]")
                self.print_rich("[dim]Notes need publish: true in frontmatter.[/dim]")
                return
            self.print_rich(f"[bold]Publishable notes ({len(posts)}):[/bold]")
            for p in posts:
                tags = ", ".join(p["tags"]) if p["tags"] else ""
                self.print_rich(f"  {p['date']}  [bold]{p['title']}[/bold]  [dim]{tags}[/dim]")

        elif action == "build":
            label = (site or self._active_site()).get("name", "site")
            self.print_rich(f"[bold]Building {label}...[/bold]")
            stats = self.build(site)
            if "error" in stats:
                self.print_rich(f"[red]{stats['error']}[/red]")
            else:
                self.print_rich(f"[green]Built {stats['pages']} pages, {stats['posts']} posts, {stats['tags']} tags, {stats['images']} images[/green]")
                self.print_rich(f"[dim]Output: {stats['output']}[/dim]")

        elif action == "deploy":
            label = (site or self._active_site()).get("name", "site")
            self.print_rich(f"[bold]Deploying {label}...[/bold]")
            result = await self.deploy(site)
            if "error" in result:
                self.print_rich(f"[red]{result['error']}[/red]")
            elif result.get("status") == "nothing_changed":
                self.print_rich(f"[dim]{result['message']}[/dim]")
            else:
                self.print_rich(f"[green]Deployed! {result['url']}[/green]")

        else:
            self.print_rich("[dim]Usage: eos publish {list|build|deploy|sites}[/dim]")

    # --- Sites API ---

    @web_route("GET", "/api/sites")
    async def api_sites(self, request):
        """List all site profiles."""
        sites = self._load_sites()
        active_id = self._active_site_id()
        return {
            "sites": sites,
            "active": active_id,
        }

    @web_route("POST", "/api/sites")
    async def api_create_site(self, request):
        """Create a new site profile."""
        data = await request.json()
        name = data.get("name", "").strip()
        if not name:
            return {"error": "Site name is required"}

        site_id = slugify(name)
        sites = self._load_sites()

        existing_ids = {s["id"] for s in sites}
        base_id = site_id
        counter = 1
        while site_id in existing_ids:
            site_id = f"{base_id}-{counter}"
            counter += 1

        site = dict(_DEFAULT_SITE, id=site_id, name=name)
        for field in _SITE_FIELDS:
            if field in data:
                site[field] = data[field]

        sites.append(site)
        self._save_sites(sites)
        return {"ok": True, "site": site}

    @web_route("PUT", "/api/sites/{site_id}")
    async def api_update_site(self, request):
        """Update an existing site profile."""
        site_id = request.path_params["site_id"]
        data = await request.json()
        sites = self._load_sites()

        for s in sites:
            if s["id"] == site_id:
                for field in _SITE_FIELDS:
                    if field in data:
                        s[field] = data[field]
                self._save_sites(sites)
                return {"ok": True, "site": s}

        return {"error": f"Site '{site_id}' not found"}

    @web_route("DELETE", "/api/sites/{site_id}")
    async def api_delete_site(self, request):
        """Delete a site profile."""
        site_id = request.path_params["site_id"]
        sites = self._load_sites()

        if len(sites) <= 1:
            return {"error": "Cannot delete the last site"}

        sites = [s for s in sites if s["id"] != site_id]
        self._save_sites(sites)

        if self._active_site_id() == site_id:
            svc = self.kernel.services.get_optional("settings")
            if svc:
                svc.set("publish.active_site", sites[0]["id"])

        return {"ok": True}

    @web_route("POST", "/api/sites/activate")
    async def api_activate_site(self, request):
        """Switch active site profile."""
        data = await request.json()
        site_id = data.get("site_id", "")
        if not self._get_site(site_id):
            return {"error": f"Site '{site_id}' not found"}

        svc = self.kernel.services.get_optional("settings")
        if svc:
            svc.set("publish.active_site", site_id)
        return {"ok": True, "active": site_id}

    # --- Web API ---

    @web_route("GET", "/api/sources")
    async def api_sources(self, request):
        """List notes for active site. ?include_drafts=1 to include drafts."""
        include_drafts = request.query_params.get("include_drafts", "").lower() in ("1", "true", "yes")
        return self.scan(include_drafts=include_drafts)

    @web_route("GET", "/api/drafts")
    async def api_drafts(self, request):
        """List draft notes (publish: false) in active site's source folder."""
        all_items = self.scan(include_drafts=True)
        return [i for i in all_items if i.get("draft")]

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Get active site config, build state, and sources (single scan)."""
        site = self._active_site()
        ss = self._site_state(site)
        all_items = self.scan(site, include_drafts=True)
        items = [i for i in all_items if not i.get("draft")]
        drafts = [i for i in all_items if i.get("draft")]
        has_landing = any(
            i.get("layout") == "landing" for i in items if i["type"] == "page"
        )
        return {
            "config": self._site_config(site),
            "source_folder": self._source_folder(site),
            "site_id": site["id"],
            "site_name": site.get("name", site["id"]),
            "site_mode": "project" if has_landing else "blog",
            "page_count": sum(1 for i in items if i["type"] == "page"),
            "post_count": sum(1 for i in items if i["type"] == "post"),
            "draft_count": len(drafts),
            "sources": items,
            "drafts": drafts,
            "last_build": ss.get("last_build"),
            "last_build_stats": ss.get("last_build_stats"),
            "last_deploy": ss.get("last_deploy"),
            "deploy_repo": ss.get("deploy_repo"),
        }

    async def _site_from_request(self, request) -> dict | None:
        try:
            data = await request.json()
        except Exception:
            data = {}
        site_id = (data or {}).get("site_id") or (data or {}).get("site") or ""
        if not site_id:
            return None
        s = self._get_site(site_id)
        if not s:
            raise ValueError(f"Site '{site_id}' not found")
        return s

    @web_route("POST", "/api/build")
    async def api_build(self, request):
        """Build the static site. Body: {"site_id": "..."} optional; defaults to active."""
        try:
            site = await self._site_from_request(request)
        except ValueError as e:
            return {"error": str(e)}
        stats = self.build(site=site)
        site_id = (site or self._active_site())["id"]
        await self.emit("publish:built", {**stats, "site": site_id})
        return stats

    @web_route("POST", "/api/deploy")
    async def api_deploy(self, request):
        """Deploy a site to static hosting. Body: {"site_id": "..."} optional; defaults to active."""
        try:
            site = await self._site_from_request(request)
        except ValueError as e:
            return {"error": str(e)}
        return await self.deploy(site=site)

    async def deploy_firebase(self, site: dict | None = None) -> dict:
        """Deploy site to Firebase Hosting."""
        s = site or self._active_site()
        site_dir = self._site_dir(s)
        if not site_dir.exists() or not (site_dir / "index.html").exists():
            return {"error": "No built site found. Run build first."}

        config = self._site_config(s)
        project_id = config.get("firebase_project", "")
        if not project_id:
            svc = self.kernel.services.get_optional("settings")
            if svc:
                project_id = svc.get("publish.firebase_project", "")
        if not project_id:
            return {"error": "No Firebase project configured. Set firebase_project in site settings or publish.firebase_project in settings."}

        fb_json = site_dir / "firebase.json"
        if not fb_json.exists():
            fb_json.write_text(json.dumps({
                "hosting": {
                    "public": ".",
                    "ignore": ["firebase.json", ".git/**"],
                    "rewrites": [{"source": "**", "destination": "/index.html"}],
                }
            }, indent=2), encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            "firebase", "deploy", "--only", "hosting",
            "--project", project_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(site_dir),
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")

        if proc.returncode != 0:
            return {"error": f"Firebase deploy failed: {err or out}"}

        import re
        url_match = re.search(r"https://[\w.-]+\.web\.app", out + err)
        url = url_match.group(0) if url_match else f"https://{project_id}.web.app"

        self._save_state({
            "last_deploy": datetime.now().isoformat(),
            "deploy_target": "firebase",
            "firebase_project": project_id,
        }, s)

        await self.emit("publish:deployed", {
            "target": "firebase", "project": project_id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "site": s["id"],
        })

        return {"status": "deployed", "url": url, "target": "firebase"}

    @web_route("POST", "/api/deploy/firebase")
    async def api_deploy_firebase(self, request):
        """Deploy a site to Firebase Hosting. Body: {"site_id": "..."} optional; defaults to active."""
        try:
            site = await self._site_from_request(request)
        except ValueError as e:
            return {"error": str(e)}
        return await self.deploy_firebase(site=site)

    @web_route("GET", "/api/preview")
    async def api_preview(self, request):
        """Preview a single rendered post."""
        slug = request.query_params.get("slug", "")
        if not slug:
            return {"error": "slug is required"}

        all_items = self.scan()
        item = next((p for p in all_items if p["slug"] == slug), None)
        if not item:
            return {"error": f"Post '{slug}' not found"}

        from emptyos.sdk.markdown_render import render_markdown

        content = await self.read(item["path"])
        body_md = strip_frontmatter(content).strip()

        published_slugs = {}
        for p in all_items:
            entry = (p["slug"], p.get("type", "post"))
            published_slugs[p["slug"]] = entry
            published_slugs[Path(p["path"]).stem.lower().replace(" ", "-")] = entry

        body_html, _ = render_markdown(body_md, published_slugs)

        # Rewrite media/ src URLs to use the source-media API so the preview
        # panel can display images without needing the site to be built first.
        import re as _re
        body_html = _re.sub(
            r'src="(media/[^"]+)"',
            lambda m: f'src="/publish/api/source-media?file={m.group(1)[6:]}"',
            body_html,
        )

        return {
            "title": item["title"],
            "type": item.get("type", "post"),
            "date": item["date"],
            "tags": item["tags"],
            "html": body_html,
        }

    @web_route("GET", "/api/site-file")
    async def api_site_file(self, request):
        """Serve a file from the built site for preview."""
        from starlette.responses import Response, FileResponse

        site_id = request.query_params.get("site", "")
        site = self._get_site(site_id) if site_id else None
        path = request.query_params.get("path", "index.html")
        file_path = self._site_dir(site) / path.lstrip("/")

        try:
            file_path.resolve().relative_to(self._site_dir(site).resolve())
        except ValueError:
            return Response("Forbidden", status_code=403)

        if not file_path.exists():
            return Response("Not found", status_code=404)

        ext = file_path.suffix.lower()
        content_types = {
            ".html": "text/html", ".css": "text/css", ".xml": "application/xml",
            ".json": "application/json", ".js": "text/javascript",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
            ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
            ".mp4": "video/mp4", ".webm": "video/webm",
        }
        media_type = content_types.get(ext, "application/octet-stream")

        if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".wav", ".ogg", ".mp4", ".webm"):
            return FileResponse(str(file_path), media_type=media_type)

        content = file_path.read_text(encoding="utf-8")
        if ext == ".html":
            import re
            parent = str(Path(path).parent).replace("\\", "/")
            if parent == ".":
                parent = ""

            def rewrite(m):
                attr, quote, url = m.group(1), m.group(2), m.group(3)
                if url.startswith(("http://", "https://", "#", "mailto:", "javascript:")):
                    return m.group(0)
                if parent and url.startswith("../"):
                    resolved = str(Path(parent) / url).replace("\\", "/")
                    parts = []
                    for p in resolved.split("/"):
                        if p == "..":
                            if parts:
                                parts.pop()
                        elif p != ".":
                            parts.append(p)
                    resolved = "/".join(parts)
                elif parent:
                    resolved = parent + "/" + url
                else:
                    resolved = url
                return f'{attr}={quote}/publish/api/site-file?path={resolved}{quote}'

            content = re.sub(r'(href|src|content)=(["\'])([^"\']+)\2', rewrite, content)

        return Response(content, media_type=media_type)

    # --- Writer API ---

    @web_route("POST", "/api/ai-write")
    async def api_ai_write(self, request):
        """AI writing actions: polish, expand, compress, translate, outline, suggest_title, review."""
        data = await request.json()
        action = data.get("action", "")
        text = data.get("text", "").strip()
        if not text:
            return {"error": "No text provided"}

        if action == "review":
            focus = data.get("focus", "").strip()
            user_msg = ""
            if focus:
                user_msg += f"**Additional focus for this review:** {focus}\n\n"
            user_msg += "Article:\n\n" + text
            result = await self.think(
                user_msg, domain="text", system=REVIEW_PROMPT_HEADER,
            )
            return {"review": result, "action": "review", "provenance": self.last_provenance()}

        if action == "apply_review":
            review = data.get("review", "").strip()
            if not review:
                return {"error": "No review provided"}
            user_msg = f"REVIEW:\n\n{review}\n\nARTICLE:\n\n{text}"
            result = await self.think(
                user_msg, domain="text", system=APPLY_REVIEW_PROMPT,
            )
            return {"text": result, "action": "apply_review", "provenance": self.last_provenance()}

        if action == "chat":
            message = data.get("message", "").strip()
            history = data.get("history", [])
            if not message:
                return {"error": "No message provided"}
            history_str = ""
            for turn in history[-10:]:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                history_str += f"\n{role.upper()}: {content}\n"
            prompt = (
                f"ARTICLE:\n\n{text}\n\n"
                f"---\n\nCONVERSATION HISTORY:{history_str}\n\n"
                f"---\n\nUSER MESSAGE: {message}\n\n"
                f"Respond as JSON: {{\"reply\": \"...\", \"revised_article\": \"...\" or null}}"
            )
            result = await self.think(
                prompt, domain="text", system=CHAT_SYSTEM_PROMPT,
            )
            parsed = parse_llm_json(result, {"reply": "", "revised_article": None})
            return {
                "reply": parsed.get("reply", ""),
                "revised_article": parsed.get("revised_article"),
                "action": "chat",
                "provenance": self.last_provenance(),
            }

        prompts = {
            "polish": POLISH_PROMPT + text,
            "expand": EXPAND_PROMPT + text,
            "compress": COMPRESS_PROMPT + text,
            "translate": TRANSLATE_PROMPT + text,
            "outline": OUTLINE_PROMPT + text,
        }

        if action == "suggest_title":
            result = await self.think(
                SUGGEST_TITLE_PROMPT + text[:3000],
                system=WRITER_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            parsed = parse_llm_json(result, {"title": "", "summary": ""})
            parsed["provenance"] = self.last_provenance()
            return parsed

        prompt = prompts.get(action)
        if not prompt:
            return {"error": f"Unknown action: {action}"}

        result = await self.think(prompt, system=WRITER_SYSTEM, domain="text", temperature=0.5)
        return {"text": result, "action": action, "provenance": self.last_provenance()}

    @web_route("POST", "/api/toggle-publish")
    async def api_toggle_publish(self, request):
        """Flip frontmatter publish: between true/false for a vault note."""
        data = await request.json()
        raw_path = (data.get("path") or "").strip()
        target = bool(data.get("publish"))
        if not raw_path:
            return {"error": "path is required"}

        vault = self._vault_dir()
        file_path = Path(raw_path)
        if not file_path.is_absolute():
            file_path = Path(vault) / raw_path

        try:
            file_path.resolve().relative_to(Path(vault).resolve())
        except ValueError:
            return {"error": "Path outside vault"}
        if not file_path.exists():
            return {"error": "File not found"}

        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            return {"error": "Note has no frontmatter"}

        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            return {"error": "Malformed frontmatter"}

        new_value = "true" if target else "false"
        replaced = False
        for i in range(1, end_idx):
            line = lines[i]
            stripped = line.lstrip()
            if stripped.startswith("publish:"):
                indent = line[: len(line) - len(stripped)]
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                lines[i] = f"{indent}publish: {new_value}{newline}"
                replaced = True
                break
        if not replaced:
            lines.insert(end_idx, f"publish: {new_value}\n")

        file_path.write_text("".join(lines), encoding="utf-8")
        return {"ok": True, "path": str(file_path), "publish": target}

    @web_route("POST", "/api/save-draft")
    async def api_save_draft(self, request):
        """Save content to vault as a markdown file."""
        data = await request.json()
        title = data.get("title", "").strip()
        content = data.get("content", "")
        existing_path = data.get("path", "")

        if not title:
            return {"error": "Title is required"}

        if existing_path and Path(existing_path).exists():
            Path(existing_path).write_text(content, encoding="utf-8")
            return {"ok": True, "path": existing_path}

        vault = self._vault_dir()
        source = self._source_folder()
        folder = Path(vault) / source
        folder.mkdir(parents=True, exist_ok=True)

        slug = slugify(title)
        file_path = folder / f"{slug}.md"

        counter = 1
        while file_path.exists():
            file_path = folder / f"{slug}-{counter}.md"
            counter += 1

        file_path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(file_path)}

    @web_route("GET", "/api/load-post")
    async def api_load_post(self, request):
        """Load raw markdown content of a vault note."""
        path = request.query_params.get("path", "")
        if not path:
            return {"error": "path is required"}

        vault = self._vault_dir()
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(vault) / path

        try:
            file_path.resolve().relative_to(Path(vault).resolve())
        except ValueError:
            return {"error": "Path outside vault"}

        if not file_path.exists():
            return {"error": "File not found"}

        content = file_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content).strip()

        # Preserve the raw frontmatter block so the writer doesn't drop fields it
        # doesn't render (cover, featured, custom).
        raw_fm = ""
        if content.startswith("---\n"):
            close = content.find("\n---\n", 4)
            if close != -1:
                raw_fm = content[4:close]

        return {
            "content": body,
            "title": fm.get("title", file_path.stem.replace("-", " ").title()),
            "tags": fm.get("tags", []),
            "type": fm.get("type", "post"),
            "summary": fm.get("summary", ""),
            "image_prompt": fm.get("image_prompt", ""),
            "raw_frontmatter": raw_fm,
            "path": str(file_path),
        }

    @web_route("POST", "/api/suggest-topics")
    async def api_suggest_topics(self, request):
        """AI scans vault and suggests content worth publishing."""
        vault = self._vault_dir()
        if not vault:
            return {"error": "No vault configured"}

        scan_raw = self.vault_config("scan_folders", "30_Resources,20_Areas,10_Projects")
        scan_folders = [f.strip() for f in scan_raw.split(",")]
        candidates = []

        for folder in scan_folders:
            folder_path = Path(vault) / folder
            if not folder_path.exists():
                continue
            for md in folder_path.rglob("*.md"):
                if md.name.startswith(("_", ".", "%", "$")):
                    continue
                try:
                    content = md.read_text(encoding="utf-8")[:500]
                except Exception:
                    continue
                fm = parse_frontmatter(content)
                if str(fm.get("publish", "")).lower() in ("true", "yes"):
                    continue
                tags = fm.get("tags", [])
                if isinstance(tags, list) and "private" in tags:
                    continue
                title = fm.get("title", md.stem.replace("-", " "))
                candidates.append(f"- {title} ({md.relative_to(Path(vault))})")

        if not candidates:
            return {"topics": [], "message": "No candidates found"}

        sample = "\n".join(candidates[:50])

        result = await self.think(
            SUGGEST_TOPICS_PROMPT + sample,
            system=WRITER_SYSTEM,
            domain="text",
            temperature=0.4,
        )

        topics = parse_llm_json(result, [])
        return {"topics": topics if isinstance(topics, list) else [], "provenance": self.last_provenance()}


    # --- Chatbot Q&A: admin proxies + faqs.toml writer (see chatbot.py) ---
    _chatbot_admin_creds = _chatbot._chatbot_admin_creds
    _chatbot_admin_request = _chatbot._chatbot_admin_request
    api_chatbot_qa_list = _chatbot.api_chatbot_qa_list
    api_chatbot_qa_update = _chatbot.api_chatbot_qa_update
    api_chatbot_qa_promote = _chatbot.api_chatbot_qa_promote
    api_chatbot_faqs_list = _chatbot.api_chatbot_faqs_list
    _faqs_path = _chatbot._faqs_path
    _read_faqs = _chatbot._read_faqs
    _append_faq = _chatbot._append_faq

    # --- Media: cover generation, podcast embedding (see media.py) ---
    api_podcast_status = _media.api_podcast_status
    api_source_media = _media.api_source_media
    api_source_media_file = _media.api_source_media_file
    api_cover_status = _media.api_cover_status
    api_generate_cover = _media.api_generate_cover
    api_approve_cover = _media.api_approve_cover
    api_reject_cover = _media.api_reject_cover
    api_generate_podcast = _media.api_generate_podcast
    _consult_or_fallback = _media._consult_or_fallback
    _set_frontmatter_field = _media._set_frontmatter_field
    _download_cover_image = _media._download_cover_image
    _insert_cover_marker = _media._insert_cover_marker
    _render_slideshow_video = _media._render_slideshow_video
    _podcast_embed_code = _media._podcast_embed_code
