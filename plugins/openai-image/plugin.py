"""OpenAI cloud providers — gpt-image-1 (draw) + gpt-4o-mini (think w/ vision).

Both are registered ONLY in the `cloud-explicit` domain — never in the
default chain. Apps must opt in per call (`domain="cloud-explicit"`) so the
free local providers never get bypassed accidentally and every cloud call
passes through the consent gate (`is_cloud=True`).

- draw → gpt-image-1, ~$0.04 per 1024² render
- think → gpt-4o-mini with vision support, ~$0.001 per image-grounding call

Both share the same OPENAI_API_KEY.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from pathlib import Path

import aiohttp

from emptyos.sdk import BasePlugin


CLOUD_DOMAIN = "cloud-explicit"
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_VISION_MODEL = "gpt-4o-mini"


class OpenAIImagePlugin(BasePlugin):
    name = "openai-image"

    async def connect(self):
        from emptyos.capabilities import Provider

        plugin = self

        class OpenAIDrawProvider(Provider):
            name = "openai-gpt-image-1"
            host = "api.openai.com"
            is_cloud = True

            async def available(self) -> bool:
                return bool(os.environ.get("OPENAI_API_KEY", "").strip())

            async def health(self) -> dict:
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    return {
                        "available": False,
                        "reason": "OPENAI_API_KEY not set",
                        "recovery": {
                            "kind": "config",
                            "id": "openai-image",
                            "hint": "Set OPENAI_API_KEY in environment to enable.",
                        },
                    }
                return {"available": True, "reason": None, "recovery": None}

            def consent_summary(self, **kwargs) -> str:
                prompt = (kwargs.get("prompt") or "")[:200]
                return (
                    f"Send image-gen prompt to OpenAI {OPENAI_IMAGE_MODEL} "
                    f"(~$0.04 per 1024² image): {prompt!r}"
                )

            async def execute(self, *, prompt: str, **kwargs) -> str:
                return await plugin.generate(prompt, **kwargs)

        provider = OpenAIDrawProvider()
        draw_cap = self.kernel.capabilities.get("draw")
        if draw_cap is not None:
            existing = list(draw_cap._domains.get(CLOUD_DOMAIN, []))  # noqa: SLF001
            if not any(p.name == provider.name for p in existing):
                existing.append(provider)
                draw_cap.add_domain(CLOUD_DOMAIN, existing)
                print(f"[openai-image] draw → {CLOUD_DOMAIN}")

        # Vision-aware think provider — same key, gpt-4o-mini for image grounding
        class OpenAIVisionProvider(Provider):
            name = "openai-gpt-4o-mini-vision"
            host = "api.openai.com"
            is_cloud = True

            async def available(self) -> bool:
                return bool(os.environ.get("OPENAI_API_KEY", "").strip())

            async def health(self) -> dict:
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    return {
                        "available": False,
                        "reason": "OPENAI_API_KEY not set",
                        "recovery": {
                            "kind": "config",
                            "id": "openai-image",
                            "hint": "Set OPENAI_API_KEY to enable cloud vision.",
                        },
                    }
                return {"available": True, "reason": None, "recovery": None}

            def consent_summary(self, **kwargs) -> str:
                msgs = kwargs.get("messages") or []
                has_image = any(
                    isinstance(m.get("content"), list)
                    and any(b.get("type") == "image_url" for b in m["content"])
                    for m in msgs
                )
                shape = "image + text" if has_image else "text"
                return (
                    f"Send {shape} to OpenAI {OPENAI_VISION_MODEL} "
                    f"(~$0.001 per call)"
                )

            async def execute(
                self, *,
                prompt: str = "", system: str = "",
                messages: list[dict] | None = None,
                **kwargs,
            ) -> str:
                return await plugin.vision_chat(
                    prompt=prompt, system=system, messages=messages, **kwargs,
                )

        vision_provider = OpenAIVisionProvider()
        think_cap = self.kernel.capabilities.get("think")
        if think_cap is not None:
            existing = list(think_cap._domains.get(CLOUD_DOMAIN, []))  # noqa: SLF001
            if not any(p.name == vision_provider.name for p in existing):
                existing.append(vision_provider)
                think_cap.add_domain(CLOUD_DOMAIN, existing)
                print(f"[openai-image] think (vision) → {CLOUD_DOMAIN}")

    async def disconnect(self):
        pass

    async def vision_chat(
        self,
        *,
        prompt: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 800,
        **_kwargs,
    ) -> str:
        """Forward to OpenAI chat completions with a vision-capable model.
        Pass `messages` with image_url content blocks for visual grounding,
        or plain prompt+system for text-only calls.
        """
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        if messages:
            msgs = list(messages)
        else:
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_VISION_MODEL,
                    "messages": msgs,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"OpenAI HTTP {resp.status}: {text[:300]}")
                payload = await resp.json()
        choices = (payload or {}).get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI returned no choices")
        return (choices[0].get("message") or {}).get("content") or ""

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        **_kwargs,
    ) -> str:
        """Render via OpenAI gpt-image-1; persist bytes to a local file in
        a known cache dir; return the local file path so the calling app
        can read/copy it. Path is stable for one process session.
        """
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_IMAGE_MODEL,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                },
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"OpenAI HTTP {resp.status}: {text[:300]}")
                payload = await resp.json()

        items = (payload or {}).get("data") or []
        if not items:
            raise RuntimeError("OpenAI returned no image data")
        b64 = items[0].get("b64_json") or ""
        if b64:
            png_bytes = base64.b64decode(b64)
        else:
            url = items[0].get("url") or ""
            if not url:
                raise RuntimeError("OpenAI returned neither b64 nor url")
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    png_bytes = await r.read()

        cache_dir = Path(tempfile.gettempdir()) / "eos-openai-image"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Use a hash of the prompt for the filename so concurrent calls don't collide.
        import hashlib
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
        target = cache_dir / f"{digest}.png"
        target.write_bytes(png_bytes)
        return str(target)
