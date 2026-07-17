"""Publish — turn vault notes into a public static website.

Supports multiple site profiles (blog, docs, portfolio, etc.)
stored in sites.json. Each site has its own source folder, theme,
deploy target, and output directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import (
    parse_frontmatter,
    parse_llm_json,
    slugify,
    strip_frontmatter,
)

from . import assets as _assets
from . import chatbot as _chatbot
from . import deploy as _deploy
from . import editing as _editing
from . import folders as _folders
from . import framework as _framework
from . import media as _media
from . import scheduling as _scheduling
from . import writer as _writer
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
    "deploy_target": "github",  # which host the single Deploy button targets: "github" | "firebase"
    "firebase_project": "",  # Firebase project id (required when deploy_target == "firebase")
    "languages": "",
    "original_language": "en",
    "favicon": "",  # filename inside source_folder (e.g. "favicon.svg")
    # Mirror posts into posts/drafts/ + posts/published/ to match their
    # publish: flag. The flag stays the source of truth (scan reads the flag,
    # not the folder); the folder is a tidy mirror. false → keep posts flat.
    "split_drafts": True,
    "search_engines": True,  # false → noindex meta + disallow-all robots.txt
    "analytics": {
        "enabled": False,
        "collector_url": "",
    },  # {enabled, collector_url (blank → inherit global)}
    "chatbot": {
        "enabled": False,
        "endpoint": "",  # e.g. "https://chat.binbian.net"
        "persona": "",  # extra system-prompt text appended at service side
        "daily_cap_usd": 2.0,  # per-site daily $ ceiling enforced by the chat service
        "starter_questions": [],  # 3-4 chips shown on first widget open
        "model": "gpt-5-nano",
    },
}

# Whitelist of per-site config keys. `_site_config()` filters site dicts down
# to these keys before handing them to the builder — any key not listed here
# is silently dropped, even if it's present in `data/apps/publish/sites.json`
# and in the API response. When adding a new site-level config field
# (`hero_*`, alternate themes, build flags, etc.), ALSO add the key here or
# the builder will receive `None` and the feature will appear to silently
# no-op.
_SITE_FIELDS = [
    "name",
    "source_folder",
    "site_name",
    "site_description",
    "author",
    "author_bio",
    "social_links",
    "theme",
    "domain",
    "repo",
    "deploy_target",
    "firebase_project",
    "languages",
    "original_language",
    "favicon",
    "split_drafts",
    "search_engines",
    "analytics",
    "template",
    "chatbot",
    # static-mirror mode (pre-built HTML sites like brand/plekto/site/ → Cloudflare Pages).
    # When mode == "static-mirror", source_repo_path replaces source_folder + the markdown
    # builder is skipped; deploy uses non-force push on the named branch and preserves
    # files listed in exclude_files (e.g. Cloudflare's auto-generated wrangler.jsonc).
    "mode",
    "source_repo_path",
    "branch",
    "exclude_files",
    # Opt-in EE-prominent hero (default site). Templates fall back to legacy
    # hero when these are blank, so other sites (eos.binbian.net) stay unchanged.
    "hero_role",
    "hero_summary",
    "hero_secondary",
    "hero_stats",
    "hero_stats_label",
    "hero_stats_2",
    "hero_stats_2_label",
    "hero_cv_url",
    "hero_case_studies_url",
]


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
            out.append(
                {
                    "name": s.get("name") or s["id"],
                    "url": f"https://{domain}",
                }
            )
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
        self._state_path().write_text(json.dumps(existing, indent=2), encoding="utf-8")

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
        if s.get("mode") == "static-mirror":
            return self._validate_static_mirror_source(s)
        stats = self._builder(s).build()
        self._save_state(
            {
                "last_build": datetime.now().isoformat(),
                "last_build_stats": stats,
            },
            s,
        )
        return stats

    # deploy / static-mirror / firebase — extracted to deploy.py (bound below)

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
                self.print_rich(
                    f"  {s['id']}{marker}  [dim]{s.get('site_name', '')}[/dim]  src={s.get('source_folder', '')}"
                )
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
                self.print_rich(
                    f"[green]Built {stats['pages']} pages, {stats['posts']} posts, {stats['tags']} tags, {stats['images']} images[/green]"
                )
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
        include_drafts = request.query_params.get("include_drafts", "").lower() in (
            "1",
            "true",
            "yes",
        )
        return self.scan(include_drafts=include_drafts)

    @web_route("GET", "/api/themes")
    async def api_themes(self, request):
        """Selectable themes: built-in THEME_VARS + design-system KB notes
        (the same `design-system-*` registry the designer app few-shots off, so
        a site theme and a designer style mean the same palette). Falls back to a
        vault scan if the kb app is unavailable."""
        from .templates import THEME_VARS

        themes = [
            {"id": k, "label": k.replace("-", " ").title(), "kind": "builtin"}
            for k in THEME_VARS
        ]
        seen: set[str] = set()
        rows = []
        try:
            res = await self.call_app("kb", "list_notes", kind="pattern", topic="ui-design")
            rows = (res or {}).get("notes", []) or []
        except Exception:
            rows = []
        ds_slugs = [
            (n.get("slug") or "").strip()
            for n in rows
            if (n.get("slug") or "").startswith("design-system-")
        ]
        if not ds_slugs:
            try:
                notes_dir = self.vault_root / "30_Resources/EmptyOS/kb/notes"
                ds_slugs = [p.stem for p in sorted(notes_dir.glob("design-system-*.md"))]
            except Exception:
                ds_slugs = []
        for slug in ds_slugs:
            if not slug or slug in seen:
                continue
            seen.add(slug)
            name = slug.replace("design-system-", "").replace("-", " ").title()
            themes.append({"id": slug, "label": f"{name} (design system)", "kind": "design-system"})
        return {"themes": themes}

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
        has_landing = any(i.get("layout") == "landing" for i in items if i["type"] == "page")
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
            "deployed_target": ss.get("deploy_target"),  # which host this site was last deployed to
            "framework_eval": self._framework_enabled(),  # dark flag: show the Evaluate affordance
        }

    async def _site_from_request(self, request) -> dict | None:
        data = await self.safe_json(request)
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
        # Self-healing folders: mirror every post into posts/drafts/ or
        # posts/published/ to match its publish: flag. Fail-soft + URL-safe
        # (slugs are folder-independent). No-op when split_drafts is off.
        folders = self.normalize_post_folders(site)
        # Self-healing diagrams: re-rasterize any images/*.svg whose shipped
        # 2x PNG is missing or older than its SVG source. Fail-soft — a
        # missing Playwright never blocks the build.
        diagrams = await self._rasterize_stale_diagrams(site)
        stats = self.build(site=site)
        if folders.get("moved"):
            stats["posts_relocated"] = folders["moved"]
        if diagrams.get("rasterized"):
            stats["diagrams_rasterized"] = diagrams["rasterized"]
        site_id = (site or self._active_site())["id"]
        await self.emit("publish:built", {**stats, "site": site_id})
        return stats

    # deploy routes (/api/deploy, /api/deploy/firebase) — extracted to deploy.py (bound below)

    @web_route("GET", "/api/preview")
    async def api_preview(self, request):
        """Preview a single rendered post."""
        slug = request.query_params.get("slug", "")
        if not slug:
            return {"error": "slug is required"}

        # Drafts included: preview exists to review a post *before* it goes live,
        # so it must see publish:false. Same draft-blindness the cover workflow had.
        all_items = self.scan(include_drafts=True)
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
        from starlette.responses import FileResponse, Response

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
            ".html": "text/html",
            ".css": "text/css",
            ".xml": "application/xml",
            ".json": "application/json",
            ".js": "text/javascript",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
        }
        media_type = content_types.get(ext, "application/octet-stream")

        if ext in (
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".webp",
            ".mp3",
            ".wav",
            ".ogg",
            ".mp4",
            ".webm",
        ):
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
                return f"{attr}={quote}/publish/api/site-file?path={resolved}{quote}"

            content = re.sub(r'(href|src|content)=(["\'])([^"\']+)\2', rewrite, content)

        return Response(content, media_type=media_type)

    # --- Deploy: git push, static-mirror, Firebase (see deploy.py) ---
    deploy = _deploy.deploy
    _new_posts_since_deploy = _deploy._new_posts_since_deploy
    _chatbot_refresh_after_deploy = _deploy._chatbot_refresh_after_deploy
    _run_git = _deploy._run_git
    _resolve_static_source = _deploy._resolve_static_source
    _validate_static_mirror_source = _deploy._validate_static_mirror_source
    _ensure_static_mirror_clone = _deploy._ensure_static_mirror_clone
    _mirror_to_site_dir = _deploy._mirror_to_site_dir
    _deploy_static_mirror = _deploy._deploy_static_mirror
    deploy_firebase = _deploy.deploy_firebase
    api_deploy = _deploy.api_deploy
    api_deploy_firebase = _deploy.api_deploy_firebase

    # --- Scheduled local release + build (deploy stays human-gated) ---
    _scheduled_posts_enabled = _scheduling._scheduled_posts_enabled
    _scheduled_for_site = _scheduling._scheduled_for_site
    _release_due_for_site = _scheduling._release_due_for_site
    scheduled_publish_posts = _scheduling.scheduled_publish_posts
    api_scheduled_posts = _scheduling.api_scheduled_posts

    # --- Posts draft↔published folder mirror (see folders.py) ---
    _split_drafts_enabled = _folders._split_drafts_enabled
    _post_root = _folders._post_root
    _post_target_dir = _folders._post_target_dir
    _new_post_dir = _folders._new_post_dir
    _mirror_post_location = _folders._mirror_post_location
    normalize_post_folders = _folders.normalize_post_folders
    api_normalize_folders = _folders.api_normalize_folders

    # --- Writer API: ai-write, topics, draft load/save, toggle (see writer.py) ---
    api_ai_write = _writer.api_ai_write
    adapt_post = _writer.adapt_post
    api_toggle_publish = _writer.api_toggle_publish
    save_draft = _writer.save_draft
    api_save_draft = _writer.api_save_draft
    api_load_post = _writer.api_load_post
    api_suggest_topics = _writer.api_suggest_topics
    _voice_block = _writer._voice_block
    api_voice_status = _writer.api_voice_status

    # --- Branding-framework draft evaluator (see framework.py) ---
    _framework_enabled = _framework._framework_enabled
    _framework_note_path = _framework._framework_note_path
    _load_framework = _framework._load_framework
    _deterministic_findings = _framework._deterministic_findings
    _grade_draft = _framework._grade_draft
    api_evaluate = _framework.api_evaluate
    api_framework_get = _framework.api_framework_get
    api_framework_seed = _framework.api_framework_seed

    # --- Element edit: preview/propose/apply/reject (see editing.py) ---
    _edit_enabled = _editing._edit_enabled
    _edit_root = _editing._edit_root
    _edit_think_fn = _editing._edit_think_fn
    _load_note_split = _editing._load_note_split
    api_edit_preview = _editing.api_edit_preview
    api_edit_propose = _editing.api_edit_propose
    api_edit_apply = _editing.api_edit_apply
    api_edit_reject = _editing.api_edit_reject

    # --- Chatbot Q&A: admin proxies + faqs.toml writer (see chatbot.py) ---
    _chatbot_admin_creds = _chatbot._chatbot_admin_creds
    _chatbot_admin_request = _chatbot._chatbot_admin_request
    api_chatbot_qa_list = _chatbot.api_chatbot_qa_list
    api_chatbot_qa_update = _chatbot.api_chatbot_qa_update
    api_chatbot_qa_promote = _chatbot.api_chatbot_qa_promote
    api_chatbot_faqs_list = _chatbot.api_chatbot_faqs_list
    api_chatbot_sync_site = _chatbot.api_chatbot_sync_site
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
    _extract_article_images = _media._extract_article_images

    # ── Asset studio (extracted to assets.py) ──
    _asset_studio_enabled = _assets._asset_studio_enabled
    _media_dir = _assets._media_dir
    _classify_ref = staticmethod(_assets._classify_ref)
    _parse_media_refs = staticmethod(_assets._parse_media_refs)
    _redaction_patterns = _assets._redaction_patterns
    _redaction_hits = _assets._redaction_hits
    api_assets_status = _assets.api_assets_status
    api_assets_audit = _assets.api_assets_audit
    api_assets_diagram_propose = _assets.api_assets_diagram_propose
    api_assets_diagram_apply = _assets.api_assets_diagram_apply
    api_assets_shot_propose = _assets.api_assets_shot_propose
    api_assets_shot_apply = _assets.api_assets_shot_apply
    api_assets_reject = _assets.api_assets_reject
    api_assets_preview = _assets.api_assets_preview
    _images_dir = _media._images_dir
    _rasterize_stale_diagrams = _media._rasterize_stale_diagrams
    api_diagrams_status = _media.api_diagrams_status
    api_diagrams_rasterize = _media.api_diagrams_rasterize
