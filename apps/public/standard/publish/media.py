"""Publish media — cover image generation and podcast embedding.

Extracted from publish/app.py to keep the core site/post lifecycle atomic.
Handles: cover generation via draw capability, cover approve/reject, podcast
generation via call_app('podcast'), slideshow video rendering, and the
frontmatter/post-body stitching helpers used by those flows.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from emptyos.sdk import web_route
from emptyos.sdk.svg_raster import rasterize_svgs, stale_svg_pairs
from emptyos.sdk.utils import parse_frontmatter, set_frontmatter_field, strip_frontmatter

from .prompts import PROMPTS

# Markdown image: ![alt](path) — captures the path. Skips wikilink-embed form
# (![[x.png]]) because the publish-app's posts already standardise on the
# alt-bearing form per the showcase markdown flip (2026-05-24).
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|gif|svg|webp))\)", re.IGNORECASE)


def _extract_article_images(self, body: str, source_folder: str) -> list[str]:
    """Pull image paths from a post body, resolve to absolute filesystem paths.

    Skips absolute URLs (http/https), data URIs, and anchors. Tries each
    relative path against a small set of plausible roots so both `media/foo.png`
    (canonical) and bare `foo.png` (some legacy posts) work. Returns paths in
    document order, deduped while preserving order. Missing files are silently
    dropped — the caller still gets the LLM-planning fallback when nothing
    resolves.
    """
    vault = Path(self._vault_dir())
    source_dir = vault / source_folder
    # `images/` first: the restructured site layout files diagrams under
    # `<source>/images/` while post bodies reference them by bare filename.
    # `media/` and the source root stay as fallbacks for legacy/canonical refs.
    roots = [source_dir / "images", source_dir, source_dir / "media", vault]
    out: list[str] = []
    seen: set[str] = set()
    for match in _MD_IMG_RE.finditer(body):
        raw = match.group(1).strip()
        if not raw or raw.startswith(("http://", "https://", "data:", "#")):
            continue
        # Try resolving against each candidate root.
        resolved: Path | None = None
        if Path(raw).is_absolute() and Path(raw).exists():
            resolved = Path(raw)
        else:
            for root in roots:
                candidate = (root / raw).resolve()
                if candidate.exists():
                    resolved = candidate
                    break
        if not resolved:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out

# Cover-pipeline persona prompts (summarizer + art-director fallbacks) live in
# prompts.py so they're tunable via /prompts; read as PROMPTS.cover_*_system.

# Positive prompt: styling only. Negation phrases live in _COVER_NEGATIVE (the model's
# negative channel) — stuffing them here bloats the positive prompt past the CLIP-L
# 77-token budget (truncating the real subject) and CLIP handles negation poorly.
_COVER_PROMPT_WRAP = (
    "{brief} "
    "Editorial blog cover illustration, bold composition, cinematic lighting, rich specific detail."
)

_COVER_NEGATIVE = (
    "text, letters, words, numbers, typography, captions, logos, watermarks, "
    "signatures, UI mockups, screenshots, frames, borders"
)

# Summarizer excerpt window. A plain body[:N] misses the thesis when the argument
# only crystallizes in the conclusion — so for long pieces we send HEAD + TAIL so the
# model sees both the setup and the payoff without an unbounded payload.
_SUMMARY_HEAD = 4000
_SUMMARY_TAIL = 2000


def _summarizer_excerpt(body: str) -> str:
    """Excerpt an article for the summarizer: whole thing if short, else head+tail."""
    body = (body or "").strip()
    if len(body) <= _SUMMARY_HEAD + _SUMMARY_TAIL:
        return body
    return f"{body[:_SUMMARY_HEAD].rstrip()}\n\n[…]\n\n{body[-_SUMMARY_TAIL:].lstrip()}"


# ------------------------------------------------------------------
# Status endpoints — what's been generated for each post?
# ------------------------------------------------------------------


@web_route("GET", "/api/podcast-status")
async def api_podcast_status(self, request):
    """Check which posts have podcasts generated."""
    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    if not media_dir.exists():
        return {"podcasts": {}}
    status = {}
    for f in media_dir.iterdir():
        if f.name.startswith("podcast-") and f.suffix == ".mp3":
            slug = f.stem.replace("podcast-", "")
            has_slideshow = (media_dir / f"podcast-{slug}-slideshow.json").exists()
            has_video = (media_dir / f"podcast-{slug}.mp4").exists()
            status[slug] = {
                "file": f.name,
                "type": "slideshow" if has_slideshow else "audio",
                "size_kb": f.stat().st_size // 1024,
                "has_slideshow": has_slideshow,
                "has_video": has_video,
            }
    return {"podcasts": status}


@web_route("GET", "/api/source-media")
async def api_source_media(self, request):
    """Serve a file from the vault source's media/ folder (pre-build preview).

    Scoped to media/ only — used by the cover preview modal before Build copies
    the asset into the site output.
    """
    from starlette.responses import FileResponse, Response

    filename = request.query_params.get("file", "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return Response("Invalid filename", status_code=400)

    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    file_path = media_dir / filename
    try:
        file_path.resolve().relative_to(media_dir.resolve())
    except ValueError:
        return Response("Forbidden", status_code=403)
    if not file_path.exists():
        return Response("Not found", status_code=404)

    ext = file_path.suffix.lower()
    types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".js": "text/javascript",
        ".json": "application/json",
    }
    return FileResponse(str(file_path), media_type=types.get(ext, "application/octet-stream"))


@web_route("GET", "/api/source-media/{filename:path}")
async def api_source_media_file(self, request):
    """Serve a file from the vault source's media/ folder via path segment (used by preview panel)."""
    from starlette.responses import FileResponse, Response

    filename = request.path_params.get("filename", "")
    if not filename or ".." in filename:
        return Response("Forbidden", status_code=403)

    source_dir = Path(self._vault_dir()) / self._source_folder()
    file_path = source_dir / "media" / filename
    try:
        file_path.resolve().relative_to(source_dir.resolve())
    except ValueError:
        return Response("Forbidden", status_code=403)
    if not file_path.exists():
        return Response("Not found", status_code=404)

    ext = file_path.suffix.lower()
    types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".js": "text/javascript",
        ".json": "application/json",
    }
    return FileResponse(str(file_path), media_type=types.get(ext, "application/octet-stream"))


@web_route("GET", "/api/cover-status")
async def api_cover_status(self, request):
    """Check which posts have cover images — distinguish pending vs embedded."""
    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    if not media_dir.exists():
        return {"covers": {}}

    # Drafts included: a cover is authored *before* the post goes live, so the
    # whole cover workflow (status/generate/approve/reject) must see publish:false.
    posts_by_slug = {p["slug"]: p["path"] for p in self.scan(include_drafts=True)}

    covers = {}
    for f in media_dir.iterdir():
        if f.name.startswith("cover-") and f.suffix == ".png":
            slug = f.stem.replace("cover-", "")
            embedded = False
            post_path = posts_by_slug.get(slug)
            if post_path:
                try:
                    embedded = "<!-- eos-cover -->" in await self.read(str(post_path))
                except OSError:
                    pass
            covers[slug] = {
                "file": f.name,
                "size_kb": f.stat().st_size // 1024,
                "embedded": embedded,
            }
    return {"covers": covers}


# ------------------------------------------------------------------
# Cover generation — draw → preview → approve/reject
# ------------------------------------------------------------------


@web_route("POST", "/api/generate-cover")
async def api_generate_cover(self, request):
    """Generate a cover image for a post via the draw capability."""
    data = await request.json()
    slug = data.get("slug", "")
    image_style = data.get("image_style", "")

    if not slug:
        return {"error": "slug is required"}

    all_items = self.scan(include_drafts=True)
    post = next((p for p in all_items if p["slug"] == slug), None)
    if not post:
        return {"error": f"Post '{slug}' not found"}

    rewrite_brief = bool(data.get("rewrite_brief", False))

    # Frontmatter carries two durable fields:
    #   summary      — the article's thesis in 2-3 sentences (reused for RSS, OG, previews)
    #   image_prompt — the visual brief for the cover (reused on regenerate)
    # Both are generated by staff consult-agents on first run, then edited by the user
    # in the note itself. We only regenerate when missing or when the user explicitly asks.
    post_path = post["path"]
    content = await self.read(post_path)
    fm = parse_frontmatter(content)
    body = strip_frontmatter(content).strip()

    summary = (fm.get("summary") or "").strip()
    if not summary:
        summary = await self._consult_or_fallback(
            "summarizer",
            _summarizer_excerpt(body) or post["title"],
            PROMPTS.cover_summarizer_system,
            temperature=0.3,
        )
        if summary:
            # Re-read INSIDE the lock: `content` above was read before the
            # summarizer ran, so the scheduled release may have flipped
            # `publish` since. The LLM call stays outside the lock.
            async with self.note_lock(post_path):
                content = await self.read(post_path)
                content = self._set_frontmatter_field(content, "summary", summary)
                await self.write(post_path, content)

    image_prompt = (fm.get("image_prompt") or "").strip()
    if rewrite_brief or not image_prompt:
        # Give the art director the actual article (excerpt), not just the thin
        # summary — otherwise the cover is built from a lossy topic sketch and
        # can't grasp the piece's real argument or specifics.
        excerpt = body[:3000].strip()
        art_input = (
            f"Title: {post['title']}\n\n"
            f"Summary: {summary or post['title']}\n\n"
            f"Article excerpt:\n{excerpt or post['title']}"
        )
        image_prompt = await self._consult_or_fallback(
            "art-director",
            art_input,
            PROMPTS.cover_art_director_system,
            temperature=0.7,
        )
        if image_prompt:
            async with self.note_lock(post_path):
                content = await self.read(post_path)
                content = self._set_frontmatter_field(content, "image_prompt", image_prompt)
                await self.write(post_path, content)

    if not image_prompt:
        image_prompt = f"An editorial illustration for an article titled '{post['title']}'."

    prompt = _COVER_PROMPT_WRAP.format(brief=image_prompt)

    try:
        comfyui = self.service("comfyui") if hasattr(self, "service") else None
    except Exception:
        comfyui = None
    if comfyui and hasattr(comfyui, "ensure_available"):
        try:
            await comfyui.ensure_available()
        except Exception:
            pass

    draw_kwargs = {"style": image_style} if image_style else {}
    draw_kwargs["negative"] = _COVER_NEGATIVE
    try:
        filename = await self.draw(prompt, **draw_kwargs)
    except RuntimeError as e:
        if "No available provider for capability" in str(e):
            raise
        return {"error": f"Image generation failed: {e}"}
    except Exception as e:
        return {"error": f"Image generation failed: {e}"}

    if not filename:
        return {"error": "Draw capability returned no image — is ComfyUI running?"}

    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    cover_name = f"cover-{slug}.png"
    cover_path = media_dir / cover_name

    if not await self._download_cover_image(str(filename), cover_path):
        return {"error": "Failed to download image from ComfyUI"}

    return {
        "ok": True,
        "slug": slug,
        "cover_file": cover_name,
        "local_cover": f"media/{cover_name}",
        "summary": summary,
        "image_prompt": image_prompt,
        "embedded": False,
    }


@web_route("POST", "/api/approve-cover")
async def api_approve_cover(self, request):
    """Embed a previously generated cover into the post's markdown."""
    data = await request.json()
    slug = data.get("slug", "")
    if not slug:
        return {"error": "slug is required"}

    all_items = self.scan(include_drafts=True)
    post = next((p for p in all_items if p["slug"] == slug), None)
    if not post:
        return {"error": f"Post '{slug}' not found"}

    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    cover_name = f"cover-{slug}.png"
    cover_path = media_dir / cover_name
    if not cover_path.exists():
        return {"error": f"No cover found for '{slug}' — generate one first"}

    embedded = await self._insert_cover_marker(post["path"], post["title"], cover_name)
    return {"ok": True, "slug": slug, "cover_file": cover_name, "embedded": embedded}


@web_route("POST", "/api/reject-cover")
async def api_reject_cover(self, request):
    """Delete a generated cover and strip any existing embed from the post."""
    data = await request.json()
    slug = data.get("slug", "")
    if not slug:
        return {"error": "slug is required"}

    media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
    cover_name = f"cover-{slug}.png"
    cover_path = media_dir / cover_name
    deleted = False
    if cover_path.exists():
        try:
            cover_path.unlink()
            deleted = True
        except OSError as e:
            return {"error": f"Failed to delete cover: {e}"}

    stripped = False
    all_items = self.scan(include_drafts=True)
    post = next((p for p in all_items if p["slug"] == slug), None)
    if post:
        try:
            import re as _re

            async with self.note_lock(post["path"]):
                content = await self.read(post["path"])
                new_content = _re.sub(
                    r"<!-- eos-cover -->.*?<!-- /eos-cover -->\s*\n",
                    "",
                    content,
                    flags=_re.DOTALL,
                )
                new_content = _re.sub(
                    r"^cover\s*:.*\n",
                    "",
                    new_content,
                    flags=_re.MULTILINE,
                )
                if new_content != content:
                    await self.write(post["path"], new_content)
                    stripped = True
        except Exception:
            pass

    return {"ok": True, "slug": slug, "deleted": deleted, "stripped": stripped}


# ------------------------------------------------------------------
# Helpers — staff consult, frontmatter, cover download/embed
# ------------------------------------------------------------------


async def _consult_or_fallback(
    self, agent_id: str, input_text: str, fallback_system: str, temperature: float = 0.5
) -> str:
    """Ask a staff consult-agent, or fall back to inline self.think() if staff isn't loaded.

    Keeps publish (core) soft-dependent on staff (personal) — works without it but
    benefits from editable persona prompts when present.
    """
    try:
        result = await self.call_app(
            "staff", "consult", agent_id=agent_id, input_text=input_text, temperature=temperature
        )
        if result:
            return str(result).strip().strip('"').strip()
    except Exception:
        pass
    try:
        raw = await self.think(
            input_text, system=fallback_system, domain="text", temperature=temperature
        )
        return (raw or "").strip().strip('"').strip()
    except Exception:
        return ""


def _set_frontmatter_field(self, content: str, key: str, value: str) -> str:
    """Insert or replace a frontmatter field; create the frontmatter block if missing.

    Publish always quotes string values (frontmatter is consumed by the static
    site builder which expects quoted strings for titles, etc.).
    """
    safe = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return set_frontmatter_field(content, key, f'"{safe}"')


async def _download_cover_image(self, filename: str, dest: Path) -> bool:
    """Download a generated image from ComfyUI to a local path.

    Thin alias over `BaseApp.download_drawn_image` — kept on the publish app
    so existing call sites in this module read naturally as cover-fetching.
    """
    return await self.download_drawn_image(filename, dest)


async def _insert_cover_marker(self, post_path: str, title: str, cover_name: str) -> bool:
    """Insert (or replace) a <!-- eos-cover --> block at top of post body.

    Also writes `cover: media/{cover_name}` into frontmatter for future
    OG:image / hero rendering. Idempotent — re-runs replace the existing block.
    """
    try:
        import re as _re

        async with self.note_lock(post_path):
            content = await self.read(str(post_path))

            content = _re.sub(
                r"<!-- eos-cover -->.*?<!-- /eos-cover -->\s*\n",
                "",
                content,
                flags=_re.DOTALL,
            )

            cover_block = (
                f"<!-- eos-cover -->\n![{title} — cover](media/{cover_name})\n<!-- /eos-cover -->\n\n"
            )

            if content.startswith("---"):
                content = set_frontmatter_field(content, "cover", f"media/{cover_name}")
                close = content.find("---", 3)
                fm_end = close + 3
                body = content[fm_end:].lstrip("\n")
                content = content[:fm_end] + "\n\n" + cover_block + body
            else:
                content = cover_block + content

            await self.write(str(post_path), content)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# Podcast generation — call podcast app, copy assets, embed player
# ------------------------------------------------------------------


@web_route("POST", "/api/generate-podcast")
async def api_generate_podcast(self, request):
    """Generate a podcast episode from a blog post via the podcast app."""
    data = await request.json()
    slug = data.get("slug", "")
    language = data.get("language", "en")
    # Default to length-aware "auto" so a long post scales to its own length
    # instead of always shipping a fixed ~3-minute intro-only episode.
    duration = data.get("duration", "auto")

    if not slug:
        return {"error": "slug is required"}

    # Drafts included: a podcast is prepared for a post before it goes live, same
    # pre-publish shape as cover generation. Build/translate stay published-only.
    all_items = self.scan(include_drafts=True)
    post = next((p for p in all_items if p["slug"] == slug), None)
    if not post:
        return {"error": f"Post '{slug}' not found"}

    content = await self.read(post["path"])
    body = strip_frontmatter(content).strip()

    word_count = len(body.split())
    if duration == "auto":
        # A podcast DISCUSSES the post, it doesn't read it aloud — target ~half
        # the source length as dialogue, capped at 24 segments (~3-4 min) so a
        # long post can't produce a 10-minute episode. The podcast app applies
        # the same ceiling + an under-delivery guard downstream.
        target_words = int(word_count * 0.5)
        words_per = 65
        segments = max(8, min(24, target_words // words_per))
    else:
        segments = {"short": 6, "medium": 12, "long": 20}.get(duration, 6)
        words_per = {"short": 50, "medium": 65, "long": 75}.get(duration, 50)

    with_video = data.get("video", True)
    # Re-use the article's own images for the slideshow when the body has
    # enough of them. Skips LLM scene-planning + ComfyUI generation, which
    # is ~$0.20 + 60s per scene; the article images are free and instantly
    # available. Falls through to LLM-planned scenes when use_article_images
    # is False or the body has zero resolvable images.
    #
    # Only pass article_images kwarg when populated — a podcast app that
    # predates this contract would TypeError on the unknown kwarg.
    use_article_images = data.get("use_article_images", True)
    article_imgs: list[str] = []
    if with_video and use_article_images:
        article_imgs = self._extract_article_images(body, self._source_folder())
    extra_kwargs: dict = {}
    if article_imgs:
        extra_kwargs["article_images"] = article_imgs
    try:
        result = await self.call_app(
            "podcast",
            "_full_generate",
            topic=post["title"],
            context=body,
            voice_a=data.get("voice_a", "emma"),
            voice_b=data.get("voice_b", "michael"),
            segments=segments,
            words=words_per,
            language=language,
            with_cover=True,
            with_video=with_video,
            image_style=data.get("image_style", "comic"),
            **extra_kwargs,
        )

        import shutil

        media_dir = Path(self._vault_dir()) / self._source_folder() / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        audio_url = result.get("full_audio", "") or result.get("audio_url", "")
        local_audio = ""

        podcast_rel = self.vault_config("podcast_dir", "30_Resources/EmptyOS/podcast")
        podcast_vault_dir = Path(self._vault_dir()) / podcast_rel

        if audio_url:
            audio_filename = audio_url.split("/")[-1]
            src = podcast_vault_dir / audio_filename
            if src.exists():
                dest = media_dir / f"podcast-{slug}.mp3"
                shutil.copy2(str(src), str(dest))
                local_audio = f"media/podcast-{slug}.mp3"

        has_slideshow = result.get("has_slideshow", False)
        scene_files = []
        slideshow_data = {}
        if has_slideshow:
            for i, img_path_str in enumerate(result.get("scene_image_paths", [])):
                if img_path_str:
                    src = Path(img_path_str)
                    if src.exists():
                        scene_name = f"podcast-{slug}-scene-{i:02d}.png"
                        shutil.copy2(str(src), str(media_dir / scene_name))
                        scene_files.append(scene_name)
                    else:
                        scene_files.append("")
                else:
                    scene_files.append("")

            slideshow_data = {
                "topic": post["title"],
                "duration_s": result.get("duration_s", 0),
                "timings": result.get("timings", []),
                "scenes": [],
            }
            for i, sc in enumerate(result.get("scenes", [])):
                slideshow_data["scenes"].append(
                    {
                        "start_ms": sc.get("start_ms", 0),
                        "end_ms": sc.get("end_ms", 0),
                        "summary": sc.get("summary", ""),
                        "image_file": scene_files[i] if i < len(scene_files) else "",
                        # "contain" for article graphs (never crop the diagram),
                        # "cover" for full-bleed AI scenes.
                        "fit": sc.get("fit", "cover"),
                    }
                )

            import json as _json

            slideshow_json_path = media_dir / f"podcast-{slug}-slideshow.json"
            slideshow_json_path.write_text(
                _json.dumps(slideshow_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            player_src = Path(__file__).parent / "static" / "slideshow-player.js"
            player_dest = media_dir / "slideshow-player.js"
            # Always refresh so player fixes (per-scene fit, etc.) propagate.
            if player_src.exists():
                shutil.copy2(str(player_src), str(player_dest))

        video_file = ""
        if has_slideshow and local_audio:
            video_file = await self._render_slideshow_video(slug, media_dir, slideshow_data)

        embed = self._podcast_embed_code(slug, local_audio, has_slideshow, scene_files, video_file)
        if embed:
            # Audio/video generation above takes minutes — the scheduled release
            # can easily land in that window, so take the lock and re-read here
            # rather than writing back anything read before the render.
            async with self.note_lock(post["path"]):
                post_content = await self.read(post["path"])
                import re

                post_content = re.sub(
                    r"\n---\n\n## Listen to this post\n.*?AI-generated podcast discussion of this article</p>\n",
                    "",
                    post_content,
                    flags=re.DOTALL,
                )
                post_content = post_content.rstrip() + "\n" + embed
                await self.write(post["path"], post_content)

        return {
            "ok": True,
            "slug": slug,
            "audio_url": audio_url,
            "local_audio": local_audio,
            "has_slideshow": has_slideshow,
            "scene_images": len([f for f in scene_files if f]),
            "has_video": bool(video_file),
            "local_video": f"media/{video_file}" if video_file else "",
            "segments": len(result.get("script", [])),
            "auto_embedded": bool(embed),
        }
    except Exception as e:
        return {"error": f"Podcast generation failed: {e}"}


async def _render_slideshow_video(self, slug: str, media_dir: Path, slideshow_data: dict) -> str:
    """Render an MP4 from slideshow scenes + podcast audio for social sharing.

    Thin wrapper over the shared SDK assembler (``emptyos.sdk.media.
    assemble_video``) — the concat-filter + per-scene fit + letterbox logic
    lives there once. No subtitles here (the audio player carries them).
    Returns the video filename (relative to media_dir), or "" on skip/failure.
    """
    scenes = [s for s in slideshow_data.get("scenes", []) if s.get("image_file")]
    if not scenes:
        return ""
    audio_path = media_dir / f"podcast-{slug}.mp3"
    if not audio_path.exists():
        return ""

    from emptyos.sdk.media import assemble_video

    video_name = f"podcast-{slug}.mp4"
    video_path = media_dir / video_name
    image_paths = [media_dir / s["image_file"] for s in scenes]
    try:
        await assemble_video(
            scenes, image_paths, str(audio_path), "", str(video_path),
            resolution=(1080, 1080), pad_color="0x0a0a1a",
        )
    except Exception:
        return ""
    return video_name if video_path.exists() else ""


def _podcast_embed_code(
    self,
    slug: str,
    audio_path: str,
    has_slideshow: bool = False,
    scene_files: list[str] | None = None,
    video_file: str = "",
) -> str:
    """Generate markdown embed code for the podcast.

    Uses relative paths — media/ at root, ../media/ from posts/ subdir.

    If has_slideshow=True, embeds the slideshow player with scene images + synced subtitles.
    Otherwise, falls back to a plain audio player.
    If video_file is provided, includes a download link for social-media sharing.
    """
    if not audio_path:
        return ""

    video_link = ""
    if video_file:
        vuid = slug.replace("-", "_") + "_vid"
        video_link = (
            f'<p id="vid-{vuid}" style="margin:6px 0 0;font-size:0.85rem">'
            f'<a href="media/{video_file}" download>&#11015; Download as video</a>'
            f' <span style="color:var(--text-muted)">— share on LinkedIn, X, etc.</span>'
            f"</p>\n"
            f"<script>(function(){{"
            f'var a=document.querySelector("#vid-{vuid} a");'
            f"if(!a)return;"
            f'if(location.pathname.indexOf("/posts/")>=0)a.href="../media/{video_file}";'
            f'else if(location.search.indexOf("path=posts/")>=0)a.href="/publish/api/site-file?path=media/{video_file}";'
            f"}})();</script>\n"
        )

    if has_slideshow and scene_files:
        uid = slug.replace("-", "_")
        return (
            f"\n---\n\n"
            f"## Listen to this post\n\n"
            f'<div id="podcast-{uid}" style="margin:12px 0"></div>\n'
            f"<noscript>\n"
            f'<audio controls style="width:100%">\n'
            f'  <source src="{audio_path}" type="audio/mpeg">\n'
            f"</audio>\n"
            f"</noscript>\n"
            f"<script>\n"
            f"(function(){{\n"
            f'  var el = document.getElementById("podcast-{uid}");\n'
            f'  var mb = (location.pathname.indexOf("/posts/") >= 0) ? "../media/" : (location.search.indexOf("path=posts/") >= 0) ? "/publish/api/site-file?path=media/" : "media/";\n'
            f"  function go(d) {{\n"
            f'    d.audioUrl = mb + "podcast-{slug}.mp3";\n'
            f"    (d.scenes || []).forEach(function(sc) {{ if (sc.image_file) sc.image_url = mb + sc.image_file; }});\n"
            f"    SlideshowPlayer.create(el, d);\n"
            f"  }}\n"
            f"  function load() {{\n"
            f'    fetch(mb + "podcast-{slug}-slideshow.json").then(function(r) {{ return r.json(); }}).then(go);\n'
            f"  }}\n"
            f"  if (window.SlideshowPlayer) {{ load(); return; }}\n"
            f'  var s = document.createElement("script");\n'
            f'  s.src = mb + "slideshow-player.js";\n'
            f"  s.onload = load;\n"
            f"  document.head.appendChild(s);\n"
            f"}})();\n"
            f"</script>\n"
            f"{video_link}"
            f'<p style="font-size:0.8rem;color:var(--text-muted)">'
            f"AI-generated podcast discussion of this article</p>\n"
        )
    else:
        return (
            f"\n---\n\n"
            f"## Listen to this post\n\n"
            f'<audio controls style="width:100%;margin:12px 0">\n'
            f'  <source src="{audio_path}" type="audio/mpeg">\n'
            f"</audio>\n"
            f'<p style="font-size:0.8rem;color:var(--text-muted)">'
            f"AI-generated podcast discussion of this article</p>\n"
        )


# ------------------------------------------------------------------
# Article diagrams — SVG source -> 2x PNG pair
# (standard: vault CLAUDE.md § "Article diagram standard")
# ------------------------------------------------------------------


def _images_dir(self, site: dict | None = None) -> Path:
    s = site or self._active_site()
    return Path(self._vault_dir()) / (s.get("source_folder") or "") / "images"


async def _rasterize_stale_diagrams(self, site: dict | None = None) -> dict:
    """Re-rasterize images/**.svg whose sibling .png is missing or older.

    Fail-soft — never raises. The build hook calls this on every build, and a
    missing Playwright (or a render failure) must not break the site build.
    Playwright's sync API can't run inside the event loop, hence to_thread.
    """
    stale = stale_svg_pairs(self._images_dir(site))
    if not stale:
        return {"rasterized": []}
    try:
        pngs = await asyncio.to_thread(rasterize_svgs, stale)
    except ImportError:
        return {"rasterized": [], "skipped": "playwright not installed"}
    except Exception as e:
        return {"rasterized": [], "error": str(e)}
    return {"rasterized": [p.name for p in pngs]}


@web_route("GET", "/api/diagrams")
async def api_diagrams_status(self, request):
    """List a site's SVG/PNG diagram pairs + staleness. ?site_id= optional."""
    site_id = request.query_params.get("site_id", "")
    site = self._get_site(site_id) if site_id else self._active_site()
    if not site:
        return {"error": f"Site '{site_id}' not found"}
    images_dir = self._images_dir(site)
    stale = set(stale_svg_pairs(images_dir))
    diagrams = []
    if images_dir.is_dir():
        for svg in sorted(images_dir.rglob("*.svg")):
            png = svg.with_suffix(".png")
            diagrams.append(
                {
                    "svg": svg.name,
                    "png": png.name if png.exists() else None,
                    "stale": svg in stale,
                }
            )
    return {
        "site": site["id"],
        "images_dir": str(images_dir),
        "diagrams": diagrams,
        "stale_count": sum(1 for d in diagrams if d["stale"]),
    }


@web_route("POST", "/api/diagrams/rasterize")
async def api_diagrams_rasterize(self, request):
    """Rasterize stale SVG->PNG diagram pairs. Body: {"site_id": "..."} optional."""
    try:
        site = await self._site_from_request(request)
    except ValueError as e:
        return {"error": str(e)}
    result = await self._rasterize_stale_diagrams(site)
    if result.get("rasterized"):
        await self.emit(
            "publish:diagrams_rasterized",
            {"site": (site or self._active_site())["id"], "files": result["rasterized"]},
        )
    return result


# ── Figure receive (viz static-figure export) ────────────────────────────
#
# The producing app asks publish where its diagrams live and what the body line
# should be; publish owns both answers because `source_folder` is per-site and
# settings-driven, and the `![alt|637](name.png)` form is this site's house
# rule. Bind on PublishApp as `figure_asset_path` / `attach_figure`.

# Display width every article diagram is referenced at (house standard).
FIGURE_DISPLAY_WIDTH = 637


def figure_asset_path(self, post: str = "", concept: str = "") -> str:
    """Vault-relative `.svg` path for an article diagram, or "".

    Naming is `<post-shortname>-<concept>.svg` under the site's `images/`, so
    one article's assets share a prefix and sort as a block. The build hook
    (`_rasterize_stale`) derives the shipped 2x PNG on the next build, which is
    why nothing here writes a PNG.
    """
    p, c = (post or "").strip(), (concept or "").strip()
    if not p or not c:
        return ""
    return f"{self._source_folder()}/images/{p}-{c}.svg"


async def attach_figure(
    self, post: str = "", concept: str = "", asset: str = "", png: str = "",
    alt: str = "", viz_id: str = "",
) -> dict:
    """Hand back the body line for an already-written figure. Places nothing.

    Deliberately does NOT splice the line into the post. Two reasons, both from
    the house standard: *where* a diagram belongs is editorial (it illustrates
    a structure the prose walks through, and only the author knows which
    paragraph that is), and the alt text is the article's text fallback —
    the takeaway a screen reader and an RSS reader get instead of the image.
    Auto-writing either would produce a figure in the wrong place carrying a
    prompt fragment as its alt, which is worse than no figure.

    So: `embedded` is False by design, and `markdown` is a ready line to place.
    """
    if not asset:
        return {"ok": False, "error": "asset is required"}
    name = Path(png or asset).with_suffix(".png").name
    # The builder resolves bare filenames recursively, so reference the name
    # only. Square brackets inside the alt would truncate the embed at the
    # first `]` and render the whole line as literal text.
    caption = (alt or f"{post} {concept}".strip()).replace("[", "(").replace("]", ")")
    return {
        "ok": True,
        "embedded": False,
        "markdown": f"![{caption}|{FIGURE_DISPLAY_WIDTH}]({name})",
        "asset": asset,
        "png": png,
        "note": "Place this line yourself, and rewrite the alt as the diagram's takeaway.",
    }
