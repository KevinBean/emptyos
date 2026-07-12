"""Vision helpers — turn vault image paths into data URLs for the think providers.

Vault-relative image paths get base64-encoded in-line so the provider chain (which
has no vault access of its own) can pass them straight to the OpenAI/Ollama API.
External URLs (http/https) flow through unchanged.

Vision-eligible providers today (see ``openai_compat._VISION_MODEL_PATTERNS``):
OpenAI 4o / 4.1 / 5.x, Ollama llava/qwen2.5vl/llama3.2-vision/minicpm-v family.
Other tiers raise on receipt so the assistant can surface a clear "switch tier"
error rather than silently dropping the image.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Cap per-image bytes before base64-encoding. 12MB raw → ~16MB base64; covers
# most camera-roll dumps while keeping ws frames a sane size. OpenAI's hard cap
# is 20MB per image.
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def is_image_path(path: str) -> bool:
    if not path:
        return False
    if path.startswith(("http://", "https://", "data:")):
        return path.startswith("data:image/") or _looks_like_image_url(path)
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _looks_like_image_url(url: str) -> bool:
    lower = url.lower().split("?", 1)[0]
    return any(lower.endswith(ext) for ext in IMAGE_EXTENSIONS)


def path_to_data_url(vault_root: str | Path, rel_path: str) -> str | None:
    """Return a `data:<mime>;base64,...` URL or None if the file is missing / too big.

    rel_path may be either a vault-relative path or an http(s) URL — the latter
    flows through unchanged so the provider can fetch it directly.
    """
    if not rel_path:
        return None
    if rel_path.startswith(("http://", "https://", "data:")):
        return rel_path
    abs_path = Path(vault_root) / rel_path
    if not abs_path.is_file():
        return None
    size = abs_path.stat().st_size
    if size == 0 or size > MAX_IMAGE_BYTES:
        return None
    mime, _ = mimetypes.guess_type(str(abs_path))
    if not mime or not mime.startswith("image/"):
        return None
    b64 = base64.b64encode(abs_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_images(vault_root: str | Path, paths: list[str] | None) -> list[str]:
    """Convert a list of vault-relative paths (or URLs) into ready-to-send URLs.

    Silently drops items that don't resolve to an image so the model never sees
    a half-formed reference. Caller may compare ``len(out) < len(paths)`` to
    detect drops and surface a warning.
    """
    if not paths:
        return []
    out: list[str] = []
    for p in paths:
        url = path_to_data_url(vault_root, p)
        if url:
            out.append(url)
    return out
