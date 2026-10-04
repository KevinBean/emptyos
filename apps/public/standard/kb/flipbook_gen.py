"""Generation mixin — page + image generation, caching, vision-anchor refine.

Ported from apps/explore/generation.py during the kb⇔explore merge. Owns
the LLM-driven page-generation flow, per-mode artifact merge logic that
preserves the inactive mode's data across regenerations, ComfyUI/OpenAI
image rendering, and the vision-pass anchor refinement.

Cross-module: imported by app.py to compose KBApp. Vault load/save lives in
flipbook_io.py; this mixin calls those helpers via ``self.`` so generation
stays decoupled from storage details.

@web_route paths use the /api/flipbook/ prefix so they nest cleanly under
the kb app's existing /api/ surface (rather than colliding with kb's own
verbs).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import aiohttp

from emptyos.sdk import flipbook as fb
from emptyos.sdk import parse_frontmatter, parse_llm_json, web_route


class GenerationMixin:
    # Approximate cost for OpenAI gpt-4o-mini vision refine, USD per call.
    # Based on ~1700 tokens for one 1024² image + ~250 prompt tokens +
    # ~200 output tokens at $0.15/1M in, $0.60/1M out.
    OPENAI_VISION_COST_USD = 0.001
    OPENAI_VISION_MODEL = "gpt-4o-mini"

    @web_route("POST", "/api/flipbook/refine_anchors")
    async def api_refine_anchors(self, request):
        """Vision-model post-pass: relocate each callout's anchor to where its
        subject actually appears in the image.

        Two providers (explicit, never auto-fallback):
        - provider="local" (default): goes through self.think. Works if the
          active think provider accepts image content (GPT-4o, Claude 3+ via
          a vision-aware provider, vision-capable Ollama). The current
          claude-cli provider strips image content, so it'll fail there.
        - provider="openai": direct call to OpenAI gpt-4o-mini for vision.
          Costs ~$0.001 per refine. Requires OPENAI_API_KEY.
        """
        body = await request.json()
        topic = (body.get("topic") or "").strip()
        provider = (body.get("provider") or "local").strip()
        if not topic:
            return {"error": "topic required"}

        page = await self._load_page(topic)
        if not page or page.get("mode") != "image":
            return {"error": "topic not found or not an image-mode page"}
        callouts = page.get("callouts") or []
        if not callouts:
            return {"error": "no callouts to refine"}

        slug = self._slug(topic)
        folder = self._flipbook_root()
        if not folder:
            return {"error": "vault not configured"}
        img_path = folder / "_assets" / f"{slug}.png"
        if not img_path.exists():
            return {"error": "image asset missing"}
        png_bytes = img_path.read_bytes()

        # Dispatch
        if provider == "openai":
            if not os.environ.get("OPENAI_API_KEY", "").strip():
                return {
                    "error": "OPENAI_API_KEY not set; cloud unavailable.",
                    "cloud_available": False,
                }
            try:
                anchors = await self._refine_anchors_openai(png_bytes, callouts)
                cost_usd = self.OPENAI_VISION_COST_USD
                used_provider = f"openai-{self.OPENAI_VISION_MODEL}"
            except Exception as e:
                return {"error": f"OpenAI vision failed: {e}"}
        else:
            try:
                anchors = await self._refine_anchors_local(png_bytes, callouts)
                cost_usd = 0.0
                used_provider = "local-think"
            except Exception as e:
                cloud_available = bool(os.environ.get("OPENAI_API_KEY", "").strip())
                return {
                    "error": f"local vision failed: {e}",
                    "cloud_available": cloud_available,
                    "cloud_cost_usd": self.OPENAI_VISION_COST_USD,
                    "cloud_model": self.OPENAI_VISION_MODEL,
                }

        if not anchors:
            cloud_available = (
                provider != "openai" and
                bool(os.environ.get("OPENAI_API_KEY", "").strip())
            )
            return {
                "error": "vision pass returned no usable anchors",
                "cloud_available": cloud_available,
                "cloud_cost_usd": self.OPENAI_VISION_COST_USD,
                "cloud_model": self.OPENAI_VISION_MODEL,
            }

        moved = 0
        for a in fb.parse_refine_anchors(anchors, len(callouts)):
            callouts[a["idx"]]["x"] = a["x"]
            callouts[a["idx"]]["y"] = a["y"]
            moved += 1

        page["callouts"] = callouts
        await self._save_page(
            page, topic=topic, verified=page.get("verified", False),
        )
        return {
            "ok": True, "moved": moved, "callouts": callouts,
            "provider": used_provider, "cost_usd": cost_usd,
        }

    async def _refine_anchors_local(
        self, png_bytes: bytes, callouts: list[dict]
    ) -> list[dict]:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        msgs = fb.build_refine_messages(callouts, b64)
        raw = await self.think(messages=msgs, domain="reason", temperature=0.1)
        data = parse_llm_json(raw) or {}
        anchors = data.get("anchors") or []
        return anchors if isinstance(anchors, list) else []

    async def _refine_anchors_openai(
        self, png_bytes: bytes, callouts: list[dict]
    ) -> list[dict]:
        """Run vision-grounding through the cloud-explicit think domain.
        The openai-image plugin owns the actual API call + consent gating;
        we just hand it messages with image content."""
        b64 = base64.b64encode(png_bytes).decode("ascii")
        msgs = fb.build_refine_messages(callouts, b64)
        result = await self.kernel.capabilities.get("think").execute(
            messages=msgs, domain="cloud-explicit",
            temperature=0.1, max_tokens=800,
            caller_app=self.manifest.id,
        )
        raw = result.value if hasattr(result, "value") else result
        data = parse_llm_json(raw) or {}
        anchors = data.get("anchors") or []
        return anchors if isinstance(anchors, list) else []

    async def _generate_page(
        self, topic: str, parents: list[str],
        force: bool = False, mode: str = "svg", provider: str = "local",
        fast: bool = False, callout_slugs: list[str] | None = None,
        target_slug: str | None = None,
    ) -> dict:
        # ``target_slug`` (Wave 3.6) pins both the cache lookup AND the
        # save destination to a specific kb note slug — bypassing the
        # default topic→slug derivation. Used when generating a visual
        # for an existing kb concept whose title doesn't slugify to the
        # same string as the kb note's filename.
        target_path: str | None = None
        if not target_slug:
            # The resolver only runs when the caller didn't already pin a
            # destination. It hunts for kb-tagged notes whose identity
            # matches the typed topic so a flipbook generation lands on
            # the existing source note (preserving its body) rather than
            # silently overwriting whatever happens to live at the
            # topic-derived path.
            matches = await self._resolve_source_note(topic)
            if len(matches) > 1:
                return {
                    "error": "ambiguous",
                    "message": (
                        f"{len(matches)} kb notes match '{topic}'. "
                        "Pass an explicit slug to disambiguate."
                    ),
                    "candidates": [
                        {
                            "slug": m["slug"],
                            "path": m["path"],
                            "title": str(m["props"].get("title") or ""),
                        }
                        for m in matches
                    ],
                }
            if len(matches) == 1:
                target_path = matches[0]["path"]
                target_slug = matches[0]["slug"]
            else:
                # No kb-tagged match — but refuse to overwrite an existing
                # non-kb file at the slugified path. (A non-kb file there
                # is hand-authored content this subsystem doesn't own.)
                candidate_path = self._path_for(topic, parents)
                try:
                    existing = await self.read(candidate_path)
                except Exception:
                    existing = None
                if existing is not None:
                    existing_fm = parse_frontmatter(existing) or {}
                    existing_tags = existing_fm.get("tags") or []
                    if isinstance(existing_tags, str):
                        existing_tags = [
                            t.strip()
                            for t in existing_tags.split(",")
                            if t.strip()
                        ]
                    has_kb_tag = any(
                        str(t).strip().lower() == "kb"
                        for t in existing_tags
                    )
                    if not has_kb_tag:
                        return {
                            "error": "path collision",
                            "message": (
                                f"a non-kb file already exists at "
                                f"{candidate_path}; refusing to overwrite."
                            ),
                            "existing_path": candidate_path,
                        }

        cached = (
            None if force
            else await self._load_page(
                topic, parents,
                target_slug=target_slug, target_path=target_path,
            )
        )
        if cached and self._has_mode(cached, mode):
            # Cached note already has this mode's artifact — flip the
            # active view and return without hitting the LLM.
            cached["mode"] = mode
            cached["breadcrumb"] = parents + [topic]
            cached["_topic"] = topic
            cached["from_cache"] = True
            if target_slug:
                # Stamp the slug on the round-trip so subsequent saves
                # (verify / refine) keep landing on the same kb note.
                cached["_target_slug"] = target_slug
            return cached

        if parents:
            trail = " > ".join(parents)
            context = (
                f"This is a deeper page in an exploration that started at: "
                f"{trail}. Stay focused on '{topic}' as a sub-topic of "
                f"'{parents[-1]}'."
            )
        else:
            context = "This is the top of a new exploration."

        if mode == "image":
            page = await self._generate_image_page(
                topic, context, parents, provider=provider, fast=fast,
            )
        else:
            page = await self._generate_svg_page(
                topic, context, parents, callout_slugs=callout_slugs or [],
            )

        # Merge with cached so the OTHER mode's artifact survives.
        if cached:
            page = self._merge_modes(cached, page, active_mode=mode)

        try:
            await self._save_page(
                page, topic=topic, verified=False,
                target_slug=target_slug, target_path=target_path,
            )
            page["saved"] = True
        except Exception:
            pass
        if target_slug:
            # Survives the round-trip via STATE.page in the frontend so the
            # next save (verify, refine) keeps writing back to this slug.
            page["_target_slug"] = target_slug
        if target_path:
            page["_target_path"] = target_path
        return page

    @staticmethod
    def _has_mode(page: dict, mode: str) -> bool:
        if mode == "svg":
            svg = page.get("svg") or ""
            return bool(svg.strip()) and "(illustration unavailable" not in svg
        if mode == "image":
            return bool(page.get("image_url"))
        return False

    @staticmethod
    def _merge_modes(cached: dict, fresh: dict, active_mode: str) -> dict:
        """Carry the *other* mode's artifacts forward when one mode regenerates.

        Each mode keeps its own artifact (`svg`, or `image_url`+`image_prompt`)
        and its own callouts (anchor coords differ between SVG and bitmap).
        Title / subtitle / caption are pulled from the fresh page so editing
        on the active view stays current.
        """
        merged = dict(fresh)
        merged["mode"] = active_mode
        if active_mode == "svg":
            # Preserve image side from cached
            if cached.get("image_url"):
                merged["image_url"] = cached["image_url"]
            if cached.get("image_prompt"):
                merged["image_prompt"] = cached["image_prompt"]
            if cached.get("image_callouts"):
                merged["image_callouts"] = cached["image_callouts"]
        else:
            # Preserve svg side
            if cached.get("svg") and "(illustration unavailable" not in cached.get("svg", ""):
                merged["svg"] = cached["svg"]
            if cached.get("svg_callouts"):
                merged["svg_callouts"] = cached["svg_callouts"]
        return merged

    @staticmethod
    def _resolve_callout_labels(
        callout_slugs: list[str] | None,
    ) -> list[dict]:
        """kb slug→label resolution for the SDK's ``callouts_in`` param. A kb
        slug like ``snr-social-perception`` becomes the label ``snr social
        perception`` (the Wave-3 neighborhood-driven generation behaviour)."""
        out: list[dict] = []
        for s in callout_slugs or []:
            if not s:
                continue
            label = str(s).replace("-", " ").strip()
            if label:
                out.append({"label": label})
        return out

    async def _generate_svg_page(
        self, topic: str, context: str, parents: list[str],
        callout_slugs: list[str] | None = None,
    ) -> dict:
        async def _think_fn(system: str, user: str) -> str:
            raw = await self.think(user, system=system, domain="reason")
            return raw if isinstance(raw, str) else str(raw)

        page = await fb.generate_svg_page(
            topic, context=context,
            callouts_in=self._resolve_callout_labels(callout_slugs),
            symbol_catalog=self._list_symbols(),
            think_fn=_think_fn, fallback_svg=self._fallback_svg(),
        )
        # Inline the library so any <use href> resolves in the rendered output
        svg = self._inject_symbols(page.svg)
        return {
            "title": page.title,
            "subtitle": page.subtitle,
            "mode": "svg",
            "svg": svg,
            "callouts": page.callouts,
            "caption": page.caption,
            "breadcrumb": parents + [topic],
            "_topic": topic,
            "saved": False,
            "verified": False,
            "from_cache": False,
        }

    async def _generate_image_page(
        self, topic: str, context: str, parents: list[str],
        provider: str = "local", fast: bool = False,
    ) -> dict:
        async def _think_fn(system: str, user: str) -> str:
            raw = await self.think(user, system=system, domain="reason")
            return raw if isinstance(raw, str) else str(raw)

        meta = await fb.generate_image_meta(
            topic, context=context, think_fn=_think_fn,
        )
        img_prompt = meta.get("image_prompt") or topic
        image_url, image_provider, image_error = await self._render_image(
            topic, img_prompt, provider=provider, fast=fast,
        )
        cloud_available = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        return {
            "title": meta.get("title") or topic,
            "subtitle": meta.get("subtitle") or "",
            "mode": "image",
            "image_url": image_url,
            "image_prompt": img_prompt,
            "image_provider": image_provider,
            "image_error": image_error,
            "cloud_available": cloud_available,
            "callouts": meta.get("callouts") or [],
            "caption": meta.get("caption") or "",
            "breadcrumb": parents + [topic],
            "_topic": topic,
            "saved": False,
            "verified": False,
            "from_cache": False,
        }

    async def _render_image(
        self, topic: str, img_prompt: str,
        provider: str = "local", fast: bool = False,
    ) -> tuple[str, str, str]:
        """Render via the explicitly chosen provider — never auto-fallback to
        cloud. Cloud (OpenAI) costs money, so it's an opt-in per-page choice.

        Returns (image_url, provider_name, error_msg).
        """
        if provider == "openai":
            try:
                url = await self._render_openai_image(topic, img_prompt)
                return url, "openai-gpt-image-1", ""
            except Exception as e:
                return "", "", f"OpenAI: {e}"

        # Default: local ComfyUI only — no auto-fallback.
        # Fast mode: low-step render (~2-4s) — quality drops but turnaround
        # is closer to a couple of seconds vs ~10-15s for full quality.
        draw_kwargs = {}
        if fast:
            draw_kwargs.update(steps=8, cfg=1.0, width=768, height=768)
        try:
            filename = await self.draw(img_prompt, **draw_kwargs)
            url = await self._persist_image(topic, filename)
            if url:
                return url, ("comfyui-fast" if fast else "comfyui"), ""
            return "", "", "ComfyUI returned no image."
        except Exception as e:
            return "", "", f"ComfyUI: {e}"

    async def _render_openai_image(self, topic: str, prompt: str) -> str:
        """Render via OpenAI gpt-image-1 by invoking the `cloud-explicit`
        domain of the draw capability. The openai-image plugin owns the
        actual API call and the cloud-consent gate; we just receive a local
        path and copy bytes into the vault asset slot."""
        result = await self.kernel.capabilities.get("draw").execute(
            prompt=prompt, domain="cloud-explicit",
            caller_app=self.manifest.id,
        )
        src = Path(result.value)
        if not src.exists():
            raise RuntimeError("openai-image plugin returned a missing path")
        folder = self._flipbook_root()
        if folder is None:
            raise RuntimeError("vault not configured")
        asset_dir = folder / "_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(topic)
        target = asset_dir / f"{slug}.png"
        target.write_bytes(src.read_bytes())
        return f"/kb/api/flipbook/asset/{slug}.png"

    async def _persist_image(self, topic: str, comfyui_filename: str) -> str:
        """Download the ComfyUI-rendered image bytes into the vault and return
        a stable app-served URL. Makes the note self-contained — no dependency
        on ComfyUI being up after the page is generated."""
        if not comfyui_filename:
            return ""
        comfyui = self.service("comfyui")
        if not comfyui:
            return ""
        try:
            src_url = await comfyui.get_image_url(comfyui_filename)
        except Exception:
            return ""
        if not src_url:
            return ""

        folder = self._flipbook_root()
        if folder is None:
            return src_url  # fall back to direct ComfyUI URL
        asset_dir = folder / "_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(topic)
        target = asset_dir / f"{slug}.png"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(src_url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    if r.status == 200:
                        target.write_bytes(await r.read())
                    else:
                        return src_url
        except Exception:
            return src_url
        return f"/kb/api/flipbook/asset/{slug}.png"
