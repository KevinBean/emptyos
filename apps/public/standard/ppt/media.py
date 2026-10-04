"""ppt — images, narration audio, speakify + HTML export.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: image resolution (vault lookup + URL screenshot), TTS narration + sidecar hashing, speakify note rewriting, and standalone HTML export. Source of truth for a deck's asset bundle.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._path_for / self._ppt_dir (spine); _notes_hash (parser).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from pathlib import Path
from emptyos.sdk import web_route
from emptyos.sdk.utils import contained_path
from .parser import (
    DEFAULT_VISUAL_STYLE,
    SPEAKIFY_SYSTEM,
    _HR_RE,
    _IMAGE_PLACEHOLDER_RE,
    _SAY_LINE_RE,
    _STANDALONE_HTML,
    _extract_notes,
    _html_escape,
    _load_deck_js,
    _normalize_visual_style,
    _notes_hash,
    _split_frontmatter,
    parse_deck,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PptApp  # noqa: F401 — for type hints only


# ─── Bind to PptApp class as ────────────────────────────────
#   resolve_images        = _media.resolve_images
#   _resolve_one_image    = _media._resolve_one_image
#   _find_vault_image     = _media._find_vault_image
#   _screenshot_url       = _media._screenshot_url
#   narrate_deck          = _media.narrate_deck
#   set_narration         = _media.set_narration
#   speakify_deck         = _media.speakify_deck
#   _render_deck_html     = _media._render_deck_html
#   _write_vault_binary   = _media._write_vault_binary
#   export_html           = _media.export_html
#   export_pdf            = _media.export_pdf
#   api_export            = _media.api_export
#   api_resolve_images    = _media.api_resolve_images
#   api_narrate           = _media.api_narrate
#   api_narration_toggle  = _media.api_narration_toggle
#   api_speakify          = _media.api_speakify
#   api_asset             = _media.api_asset
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def resolve_images(self, id: str) -> dict:
    """Replace every image placeholder in a deck with a real image file.

    Three placeholder kinds, dispatched to different sources:

    - `![image: <prompt>]`       → AI generation via `self.draw`
    - `![vault: <name-or-glob>]` → existing image from the vault, matched
                                   by filename substring or glob
    - `![screenshot: <url>]`     → headless Playwright snapshot of any URL

    Each resolved image is copied into a per-deck assets folder
    (`<vault>/<ppt_dir>/<deck-id>/slide-NN.png`) and the placeholder is
    rewritten to `![[<filename>]]`. Idempotent — already-resolved slides
    are left alone.
    """
    import shutil

    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    raw = await self.read(path)
    matches = list(_IMAGE_PLACEHOLDER_RE.finditer(raw))
    if not matches:
        return {"resolved": 0, "skipped": 0, "errors": [], "message": "No placeholders"}

    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "No vault configured"}
    deck_assets_rel = f"{self._ppt_dir()}/{id}"
    deck_assets_dir = vault / deck_assets_rel
    deck_assets_dir.mkdir(parents=True, exist_ok=True)

    out = raw
    resolved = 0
    errors: list[str] = []
    # Walk in reverse so substring offsets stay valid as we splice.
    for i, m in enumerate(reversed(matches), 1):
        kind = m.group(1).lower()
        arg = m.group(2).strip()
        n = len(matches) - i + 1
        filename = f"slide-{n:02d}.png"
        target = deck_assets_dir / filename
        try:
            src_path = await self._resolve_one_image(kind, arg, target, vault)
            if not src_path:
                errors.append(f"slide {n} ({kind}): not resolvable")
                continue
            if str(src_path) != str(target):
                shutil.copy2(str(src_path), str(target))
            out = out[: m.start()] + f"![[{filename}]]" + out[m.end():]
            resolved += 1
        except Exception as e:
            errors.append(f"slide {n} ({kind}): {e}")

    if resolved:
        await self.write(path, out)
        self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
        await self.emit("ppt:updated", {"id": id, "field": "images"})
    return {
        "resolved": resolved,
        "skipped": len(matches) - resolved - len(errors),
        "errors": errors,
    }


async def _resolve_one_image(
    self, kind: str, arg: str, target: Path, vault: Path
) -> Path | None:
    """Dispatch one placeholder. Returns absolute source path, or `target` itself
    for sources that write directly to the destination (screenshot, image)."""
    if kind == "image":
        filename = await self.draw(arg)
        if not filename:
            return None
        ok = await self.download_drawn_image(filename, target)
        return target if ok else None
    if kind == "vault":
        return self._find_vault_image(arg, vault)
    if kind == "screenshot":
        ok = await self._screenshot_url(arg, target)
        return target if ok else None
    return None


def _find_vault_image(self, query: str, vault: Path) -> Path | None:
    """Match an image file in the vault. Accepts:
    - exact relative path: `30_Resources/foo/bar.png`
    - bare filename: `bar.png`
    - filename stem substring: `cable-diagram` (matches `cable-diagram-v3.png`)
    Searches images-only (png/jpg/jpeg/gif/svg/webp). Returns first match
    sorted by mtime desc to prefer recent edits.
    """
    q = query.strip().strip('"').strip("'")
    if not q:
        return None
    # Exact relative path
    cand = vault / q
    if cand.exists() and cand.is_file():
        return cand
    # Glob / substring against filenames anywhere in the vault
    exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
    q_lower = q.lower()
    hits: list[Path] = []
    for p in vault.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        name_lower = p.name.lower()
        if q_lower == name_lower or q_lower in name_lower:
            hits.append(p)
        if len(hits) >= 200:
            break
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


async def _screenshot_url(self, url: str, target: Path) -> bool:
    """Headless full-page screenshot via Playwright. Writes PNG to `target`.
    Public URLs only — auth-gated EmptyOS pages will capture the login page.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright not installed: pip install playwright && playwright install chromium")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(target), full_page=True)
            return target.exists()
        finally:
            await browser.close()


async def narrate_deck(self, id: str, slide_index: int | None = None) -> dict:
    """Generate per-slide TTS narration. Speaker notes → audio files.

    For each slide whose `Notes:` block is non-empty, calls
    `self.speak(notes)` and copies the resulting audio into the per-deck
    assets folder as `narration-NN.mp3`. Sets `narration: true` in the
    deck's frontmatter so the renderer auto-plays them in present mode.

    Idempotent — re-running regenerates every track. To skip slides that
    already have a track, delete the deck folder first.
    """
    import shutil

    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    deck = await self.get_deck(id)
    if "error" in deck:
        return deck

    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "No vault configured"}
    deck_dir = vault / f"{self._ppt_dir()}/{id}"
    deck_dir.mkdir(parents=True, exist_ok=True)

    slides = deck.get("slides") or []
    generated = 0
    skipped = 0
    errors: list[str] = []
    # `slide_index` is 1-based when provided (matches what the UI shows).
    # None = narrate every slide. Out-of-range = error.
    if slide_index is not None:
        if slide_index < 1 or slide_index > len(slides):
            return {"error": f"slide_index {slide_index} out of range (1-{len(slides)})"}
    for idx, slide in enumerate(slides, start=1):
        if slide_index is not None and idx != slide_index:
            continue
        # `Say:` (audience-facing) wins over `Notes:` (presenter-only).
        # Notes still acts as a fallback so legacy decks keep working.
        spoken = (slide.get("say") or slide.get("notes") or "").strip()
        if not spoken:
            skipped += 1
            continue
        try:
            src = await self.speak(spoken)
        except Exception as e:
            errors.append(f"slide {idx}: speak failed: {e}")
            continue
        if not src:
            errors.append(f"slide {idx}: speak returned empty")
            continue
        try:
            src_path = Path(src) if not isinstance(src, Path) else src
            if not src_path.exists():
                errors.append(f"slide {idx}: audio file missing at {src_path}")
                continue
            target = deck_dir / f"narration-{idx:02d}.mp3"
            shutil.copy2(str(src_path), str(target))
            # Hash sidecar — lets get_deck re-pair audio to its authoring
            # slide after a reorder or single-slide edit.
            (deck_dir / f"narration-{idx:02d}.txt").write_text(
                _notes_hash(spoken), encoding="utf-8"
            )
            generated += 1
        except Exception as e:
            errors.append(f"slide {idx}: copy failed: {e}")

    if generated:
        # Count actual mp3s on disk so per-slide narrates don't clobber
        # the total when re-running just one slide.
        total = sum(1 for _ in deck_dir.glob("narration-*.mp3"))
        self.vault_update(path, {
            "narration": True,
            "narration_count": total,
            "updated": datetime.now().strftime("%Y-%m-%d"),
        })
        await self.emit("ppt:updated", {"id": id, "field": "narration"})
    return {"generated": generated, "skipped": skipped, "errors": errors, "slide_index": slide_index}


async def set_narration(self, id: str, enabled: bool) -> dict:
    """Toggle the `narration` frontmatter flag without touching audio files.

    Off → renderer skips auto-play (audio_url not attached) but mp3s/sidecars
    remain so flipping it back on restores playback instantly.
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    self.vault_update(path, {
        "narration": bool(enabled),
        "updated": datetime.now().strftime("%Y-%m-%d"),
    })
    await self.emit("ppt:updated", {"id": id, "field": "narration_toggle"})
    return {"ok": True, "narration": bool(enabled)}


async def speakify_deck(self, id: str, slide_index: int | None = None, overwrite: bool = False) -> dict:
    """Rewrite presenter Notes into audience-facing Say: lines via think().

    Notes are coaching ("Walk through...", "Emphasize that...") — read aloud
    they sound like director's directions, not a talk. This converts each
    slide's notes into the actual sentences a speaker would say to the
    audience, in first person, then injects them as `Say:` lines into the
    deck markdown alongside the original Notes (which stay as memory aids).

    - slide_index: 1-based; None = every slide.
    - overwrite: re-speakify slides that already have a Say: line.
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    text = await self.read(path)
    fm, body = _split_frontmatter(text)
    chunks = _HR_RE.split(body)

    # Walk chunks, but only count non-empty ones as "slides" (matches
    # parse_deck's filtering so slide_index here means the same thing as
    # in narrate_deck and the UI).
    slide_positions: list[int] = []
    for i, c in enumerate(chunks):
        if c.strip():
            slide_positions.append(i)

    if slide_index is not None:
        if slide_index < 1 or slide_index > len(slide_positions):
            return {"error": f"slide_index {slide_index} out of range (1-{len(slide_positions)})"}

    rewritten = 0
    skipped = 0
    errors: list[str] = []

    for n, pos in enumerate(slide_positions, start=1):
        if slide_index is not None and n != slide_index:
            continue
        chunk = chunks[pos]
        clean_md, notes, existing_say = _extract_notes(chunk)
        if not notes:
            skipped += 1
            continue
        if existing_say and not overwrite:
            skipped += 1
            continue
        slide_text = clean_md.strip() or "(visual-only slide)"
        prompt = (
            f"Slide content:\n\n{slide_text}\n\n"
            f"Presenter notes (director's coaching, NOT to be spoken verbatim):\n\n{notes}\n\n"
            "Rewrite as the actual spoken script for this slide."
        )
        try:
            spoken = await self.think(prompt, system=SPEAKIFY_SYSTEM, temperature=0.5)
        except Exception as e:
            errors.append(f"slide {n}: think failed: {e}")
            continue
        spoken = (spoken or "").strip().strip('"').strip("'").replace("\n", " ").strip()
        if not spoken:
            errors.append(f"slide {n}: think returned empty")
            continue

        if existing_say:
            new_chunk = _SAY_LINE_RE.sub(f"Say: {spoken}", chunk, count=1)
        else:
            # Append a Say: line at the end of the chunk so it sits next to
            # any existing Notes: line.
            new_chunk = chunk.rstrip() + f"\n\nSay: {spoken}\n"
        chunks[pos] = new_chunk
        rewritten += 1

    if rewritten:
        from emptyos.runtime.vault_index import _serialize_fm
        fm["updated"] = datetime.now().strftime("%Y-%m-%d")
        # Rejoin with bare `---` — _HR_RE consumed only the dash line, not
        # the surrounding newlines, so each chunk keeps its own padding.
        new_body = "---".join(chunks)
        new_raw = _serialize_fm(fm) + "\n" + new_body.lstrip("\n")
        await self.write(path, new_raw)
        self.vault_update(path, {"updated": fm["updated"]})
        await self.emit("ppt:updated", {"id": id, "field": "speakify"})
    return {"rewritten": rewritten, "skipped": skipped, "errors": errors}


async def _render_deck_html(self, id: str) -> dict:
    """Build a deck's standalone-HTML bundle in memory — no vault write.

    Shared by `export_html` (writes the bundle to the vault) and
    `export_pdf` (renders the same bundle in headless Chromium instead):
    one source of truth for how a deck becomes a standalone document.

    Returns `{html, title, slide_count, warning?}` or `{"error": ...}`.
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    text = await self.read(path)
    fm_peek, _ = _split_frontmatter(text)
    export_base = (
        str(fm_peek.get("embed_base") or "").strip()
        or str(self.setting_or_config("ppt.export_embed_base", "", config_key="export_embed_base")).strip()
        or str(self.setting_or_config("ppt.embed_base", "", config_key="embed_base")).strip()
    )
    parsed = parse_deck(
        text,
        asset_url_prefix=f"/ppt/api/asset/{id}",
        embed_base=export_base,
    )
    deck = {
        "frontmatter": parsed["frontmatter"],
        "slides": parsed["slides"],
        "theme": parsed["frontmatter"].get("theme") or self.setting_or_config("ppt.default_theme", "dark", config_key="default_theme"),
        "aspect": parsed["frontmatter"].get("aspect") or self.setting_or_config("ppt.default_aspect", "16:9", config_key="default_aspect"),
        "visual_style": _normalize_visual_style(
            parsed["frontmatter"].get("visual_style"),
            self.setting_or_config("ppt.default_visual_style", DEFAULT_VISUAL_STYLE, config_key="default_visual_style"),
        ),
    }
    deck_js = _load_deck_js()
    if not deck_js:
        return {"error": "Renderer asset eos-deck.js not found"}
    slides_json = _json.dumps(
        [
            {"html": s["html"], "notes": s["notes"], "audio_url": s.get("audio_url", "")}
            for s in deck["slides"]
        ],
        ensure_ascii=False,
    )
    title = deck["frontmatter"].get("title") or id
    html = _STANDALONE_HTML.format(
        title=_html_escape(title),
        theme=deck["theme"],
        aspect=deck["aspect"],
        visual_style=deck["visual_style"],
        slides_json=slides_json,
        deck_js=deck_js,
    )
    result = {"html": html, "title": title, "slide_count": len(deck["slides"])}
    if not export_base:
        result["warning"] = (
            "No embed host configured (ppt.export_embed_base in Settings) — "
            "embeds in this export will point at this machine and won't resolve "
            "once the file is opened elsewhere."
        )
    return result


def _write_vault_binary(self, rel_path: str, data: bytes) -> Path:
    """Write bytes at a vault-root-relative path. Mirrors `vault_write_at`'s
    containment guarantee (never escape the vault) for binary content,
    which `vault_write_at` (text-only) can't carry."""
    p = contained_path(self.vault_root, self.vault_root / rel_path)
    if p is None:
        raise ValueError(f"path escapes the vault: {rel_path!r}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


async def export_html(self, id: str) -> dict:
    """Export a deck to standalone HTML in {vault}/30_Resources/Published/decks/.

    Embed targets are re-resolved with an export-tier `embed_base` default
    (`apps.ppt.export_embed_base`) so iframes in the standalone bundle point
    at a publicly-reachable host rather than the localhost daemon that
    rendered them. If nothing is configured, embeds fall back to the
    (unreachable-once-exported) local host and the result carries a
    `warning` explaining why.
    """
    built = await self._render_deck_html(id)
    if built.get("error"):
        return built
    decks_dir = self.vault_config("published_decks_dir", "30_Resources/Published/decks")
    published_rel = f"{decks_dir}/{id}.html"
    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "No vault configured"}
    self.vault_write_at(published_rel, built["html"])
    result = {"ok": True, "path": str(vault / published_rel), "rel": published_rel}
    if built.get("warning"):
        result["warning"] = built["warning"]
    return result


async def export_pdf(self, id: str) -> dict:
    """Export a deck to a real, multi-page PDF — one page per slide, into
    {vault}/30_Resources/Published/decks/.

    Flagged in gap analysis (ppt-no-pptx-pdf-export): `export_html` writes
    standalone HTML only, so a client/employer deliverable that must open
    outside a browser had no path out of EmptyOS. Renders the same
    standalone bundle `_render_deck_html` builds for HTML export in
    headless Chromium (`self.browse`), stepping through every slide via
    the deck renderer's exposed `window.DECK.goto(i)` and screenshotting
    each one, then assembles the PNGs into one PDF with Pillow.

    PPTX (a native PowerPoint file) is a separate, larger lift —
    `python-pptx` is not a project dependency today — and stays open as
    `ppt-no-pptx-export`.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"error": "Pillow is required for PDF export (pip install Pillow)"}

    built = await self._render_deck_html(id)
    if built.get("error"):
        return built
    slide_count = built["slide_count"]
    if not slide_count:
        return {"error": "Deck has no slides"}

    import tempfile
    import uuid

    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "No vault configured"}

    work_dir = Path(tempfile.gettempdir()) / "emptyos-ppt-pdf" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / "deck.html"
    html_path.write_text(built["html"], encoding="utf-8")
    context_id = f"ppt-pdf-{uuid.uuid4().hex[:8]}"
    png_paths: list[Path] = []
    try:
        await self.browse("navigate", url=html_path.as_uri(), context_id=context_id)
        await self.browse("wait_for", selector="#deck .deck-slide", context_id=context_id)
        for i in range(slide_count):
            await self.browse(
                "eval",
                expression=(
                    f"(async()=>{{window.DECK.goto({i});"
                    "await new Promise(r=>setTimeout(r,350));})()"
                ),
                context_id=context_id,
            )
            png_path = work_dir / f"slide-{i:03d}.png"
            await self.browse("screenshot", selector="#deck", context_id=context_id, path=str(png_path))
            png_paths.append(png_path)
    except Exception as e:  # noqa: BLE001 — surface as an export failure, not a 500
        return {"error": f"PDF render failed: {e}"}
    finally:
        try:
            await self.browse("close", context_id=context_id)
        except Exception:
            pass
        html_path.unlink(missing_ok=True)

    images = [Image.open(p).convert("RGB") for p in png_paths]
    try:
        import io

        buf = io.BytesIO()
        images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
        decks_dir = self.vault_config("published_decks_dir", "30_Resources/Published/decks")
        published_rel = f"{decks_dir}/{id}.pdf"
        try:
            out_path = self._write_vault_binary(published_rel, buf.getvalue())
        except ValueError as e:
            return {"error": str(e)}
    finally:
        for img in images:
            img.close()
        for p in png_paths:
            p.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass

    return {"ok": True, "path": str(out_path), "rel": published_rel, "slides": slide_count}


@web_route("POST", "/api/decks/{id}/export")
async def api_export(self, request):
    fmt = (request.query_params.get("format") or "html").strip().lower()
    if fmt == "pdf":
        return await self.export_pdf(request.path_params["id"])
    return await self.export_html(request.path_params["id"])


@web_route("POST", "/api/decks/{id}/resolve-images")
async def api_resolve_images(self, request):
    return await self.resolve_images(request.path_params["id"])


@web_route("POST", "/api/decks/{id}/narrate")
async def api_narrate(self, request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    idx = body.get("slide_index")
    if idx is not None:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return {"error": "slide_index must be an integer"}
    return await self.narrate_deck(request.path_params["id"], slide_index=idx)


@web_route("POST", "/api/decks/{id}/narration-toggle")
async def api_narration_toggle(self, request):
    body = await request.json()
    return await self.set_narration(request.path_params["id"], bool(body.get("enabled")))


@web_route("POST", "/api/decks/{id}/speakify")
async def api_speakify(self, request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    idx = body.get("slide_index")
    if idx is not None:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return {"error": "slide_index must be an integer"}
    return await self.speakify_deck(
        request.path_params["id"],
        slide_index=idx,
        overwrite=bool(body.get("overwrite")),
    )


@web_route("GET", "/api/asset/{deck_id}/{name}")
async def api_asset(self, request):
    """Serve an image referenced by a deck.

    Resolves `name` against (1) a per-deck assets folder and (2) the deck
    directory itself, so users can drop images either next to the .md or
    under `<deck>/assets/`. Path-traversal-safe: both `deck_id` and `name` are
    character-validated and the resolved file must stay inside the ppt dir.
    """
    from starlette.responses import FileResponse, JSONResponse

    deck_id = request.path_params["deck_id"]
    name = request.path_params["name"]
    # Both segments are interpolated into the lookup path — validate each, not
    # just `name`. A bare `deck_id` of ".." would otherwise escape ppt_dir.
    for part in (deck_id, name):
        if "/" in part or "\\" in part or part.startswith(".") or ".." in part:
            return JSONResponse({"error": "bad path"}, status_code=400)
    vault = self.kernel.config.notes_path
    if not vault:
        return JSONResponse({"error": "no vault"}, status_code=404)
    ppt_dir = vault / self._ppt_dir()
    base = ppt_dir.resolve()
    for candidate in (ppt_dir / "assets" / name, ppt_dir / name, ppt_dir / deck_id / name):
        try:
            real = candidate.resolve(strict=True)
        except OSError:
            continue
        # Defence in depth: the resolved file (symlinks followed) must stay
        # inside ppt_dir even if the segment checks above are ever loosened.
        if real.is_relative_to(base) and real.is_file():
            ext = real.suffix.lower().lstrip(".")
            mime = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "svg": "image/svg+xml",
                "webp": "image/webp",
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "m4a": "audio/mp4",
                "ogg": "audio/ogg",
                "flac": "audio/flac",
                "mp4": "video/mp4",
                "webm": "video/webm",
                "mov": "video/quicktime",
            }.get(ext, "application/octet-stream")
            return FileResponse(str(real), media_type=mime)
    return JSONResponse({"error": "not found"}, status_code=404)
