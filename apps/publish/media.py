"""Publish media — cover image generation and podcast embedding.

Extracted from publish/app.py to keep the core site/post lifecycle atomic.
Handles: cover generation via draw capability, cover approve/reject, podcast
generation via call_app('podcast'), slideshow video rendering, and the
frontmatter/post-body stitching helpers used by those flows.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import web_route
from emptyos.sdk.utils import parse_frontmatter, set_frontmatter_field, strip_frontmatter

# Fallback prompts used when the staff app isn't available.
# Primary path routes through `staff.consult("summarizer" | "art-director", ...)`.
_FALLBACK_SUMMARIZER_SYSTEM = (
    "You are an editorial summarizer. Given an article, produce a 2-3 sentence summary "
    "(max 60 words) that captures the argument — not the topic. Lead with the thesis. "
    "Avoid corporate buzzwords ('leverage', 'unlock', 'transform', 'journey'). "
    "Output the summary only. No preamble, no quotation marks."
)

_FALLBACK_ART_DIRECTOR_SYSTEM = (
    "You are an art director briefing an illustrator for an editorial cover image. "
    "Given title and summary, produce ONE concrete visual description in 2-3 sentences. "
    "Anchor on the TITLE; use the SUMMARY as thematic context. Name specific objects, "
    "composition, lighting, palette. Pick one central visual metaphor tied to the article's "
    "argument. Avoid clichés: glowing brains, neural nodes, robot hands, laptops, "
    "sunsets behind palms, lightbulb on head, brain split in two, handshakes between "
    "human and robot. No text, letters, numbers, or typography. "
    "Output the visual description only. No preamble, no quotation marks."
)

_COVER_PROMPT_WRAP = (
    "{brief} "
    "Editorial blog cover illustration. Bold composition, cinematic lighting, "
    "rich specific detail. No text, no letters, no words, no numbers, no typography, "
    "no logos, no watermarks, no UI mockups, no screenshots."
)


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

    posts_by_slug = {p["slug"]: p["path"] for p in self.scan()}

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

    all_items = self.scan()
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
            body[:4000] or post["title"],
            _FALLBACK_SUMMARIZER_SYSTEM,
            temperature=0.3,
        )
        if summary:
            content = self._set_frontmatter_field(content, "summary", summary)
            await self.write(post_path, content)

    image_prompt = (fm.get("image_prompt") or "").strip()
    if rewrite_brief or not image_prompt:
        art_input = f"Title: {post['title']}\n\nSummary: {summary or post['title']}"
        image_prompt = await self._consult_or_fallback(
            "art-director",
            art_input,
            _FALLBACK_ART_DIRECTOR_SYSTEM,
            temperature=0.7,
        )
        if image_prompt:
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

    all_items = self.scan()
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
    all_items = self.scan()
    post = next((p for p in all_items if p["slug"] == slug), None)
    if post:
        try:
            import re as _re

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
    duration = data.get("duration", "short")

    if not slug:
        return {"error": "slug is required"}

    all_items = self.scan()
    post = next((p for p in all_items if p["slug"] == slug), None)
    if not post:
        return {"error": f"Post '{slug}' not found"}

    content = await self.read(post["path"])
    body = strip_frontmatter(content).strip()

    # A two-host discussion needs ~1.5x the source words to cover all content
    word_count = len(body.split())
    if duration == "auto":
        target_words = int(word_count * 1.5)
        words_per = 60
        segments = max(6, min(24, target_words // words_per))
    else:
        segments = {"short": 6, "medium": 12, "long": 20}.get(duration, 6)
        words_per = {"short": 50, "medium": 65, "long": 75}.get(duration, 50)

    with_video = data.get("video", True)
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
            if player_src.exists() and not player_dest.exists():
                shutil.copy2(str(player_src), str(player_dest))

        video_file = ""
        if has_slideshow and local_audio:
            video_file = self._render_slideshow_video(slug, media_dir, slideshow_data)

        embed = self._podcast_embed_code(slug, local_audio, has_slideshow, scene_files, video_file)
        if embed:
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


def _render_slideshow_video(self, slug: str, media_dir: Path, slideshow_data: dict) -> str:
    """Render an MP4 from slideshow scenes + podcast audio for social sharing.

    Returns the video filename (relative to media_dir) on success, "" if skipped/failed.
    Requires ffmpeg on PATH; silently no-ops otherwise.
    """
    import shutil as _sh
    import subprocess as _sp

    ffmpeg = _sh.which("ffmpeg")
    if not ffmpeg:
        return ""

    scenes = [s for s in slideshow_data.get("scenes", []) if s.get("image_file")]
    if not scenes:
        return ""

    audio_path = media_dir / f"podcast-{slug}.mp3"
    if not audio_path.exists():
        return ""

    duration_s = float(slideshow_data.get("duration_s") or 0)

    # Build concat-demuxer file: each scene holds until the next one starts
    # (bridges the 400ms inter-segment gaps). Final scene runs to audio end.
    lines = []
    for i, sc in enumerate(scenes):
        start = sc.get("start_ms", 0) / 1000.0
        if i + 1 < len(scenes):
            end = scenes[i + 1].get("start_ms", 0) / 1000.0
        else:
            end = duration_s or (sc.get("end_ms", 0) / 1000.0)
        dur = max(0.1, end - start)
        lines.append(f"file '{sc['image_file']}'")
        lines.append(f"duration {dur:.3f}")
    # Concat demuxer quirk: repeat the last file (no duration) to flush it
    lines.append(f"file '{scenes[-1]['image_file']}'")

    concat_file = media_dir / f"_concat_{slug}.txt"
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    video_name = f"podcast-{slug}.mp4"
    video_path = media_dir / video_name
    trim_t = duration_s if duration_s > 0 else 0

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_file.name,
        "-i",
        audio_path.name,
        "-vf",
        "fps=30,format=yuv420p,scale=1080:1080:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
    ]
    if trim_t > 0:
        cmd += ["-t", f"{trim_t:.3f}"]
    cmd.append(video_name)

    try:
        _sp.run(cmd, cwd=str(media_dir), check=True, capture_output=True, timeout=900)
        return video_name if video_path.exists() else ""
    except (_sp.CalledProcessError, _sp.TimeoutExpired, OSError):
        return ""
    finally:
        try:
            concat_file.unlink()
        except OSError:
            pass


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
