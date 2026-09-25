"""Attachments for chat turns — images, documents, uploads (desktop GUI plan, B3).

Extracted from the assistant app (``files.py`` / ``vision.py`` /
``attachments.py``) when the agent became the second consumer (CLAUDE.md
rule 9); the assistant modules now re-export from here.

Four jobs, all pure except ``store_upload`` (writes one file):

- **Resolve** a vault-relative path the user attached, *confined to the vault*.
  The assistant's originals joined ``vault_root / rel_path`` unchecked, so a
  path of ``../../secret`` read outside the vault; every entry point here
  goes through ``confine``.
- **Extract** a document's text (pdf / docx / txt / md) as a fenced block.
- **Build** a user message's content in the provider family's native shape:
  OpenAI ``image_url`` parts, Anthropic ``image`` source blocks, plain text
  when there are no images.
- **Dehydrate / hydrate**: history stores an image as a path reference
  (``{"type": "eos_image", "path": …}``), not megabytes of base64, and is
  turned back into the provider's image part only when a turn replays it —
  so a provider never receives an ``eos_*`` block.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

#: Per-image raw cap before base64. OpenAI's documented hard limit is 20 MB per
#: image; 12 MB raw ≈ 16 MB encoded keeps a WS frame and a request body sane.
MAX_IMAGE_BYTES = 12 * 1024 * 1024

EXTRACTABLE_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}

#: Per-file extracted characters. 50 000 chars ≈ 12k tokens — fits every
#: supported model's context with room for history. Callers may pass their own.
DEFAULT_MAX_CHARS = 50_000

EOS_IMAGE = "eos_image"

_DATA_URL = re.compile(r"^data:(image/[\w.+-]+);base64,(.*)$", re.S)


# ── Resolving paths ────────────────────────────────────────────────


def confine(vault_root: str | Path, rel_path: str) -> Path | None:
    """``vault_root / rel_path`` resolved, or ``None`` if it leaves the vault.

    Refuses absolute paths, ``..`` escapes and symlinks pointing outside. A
    missing file is fine here (the caller checks ``is_file``); an escaping
    one is never returned.
    """
    if not vault_root or not rel_path:
        return None
    rel = str(rel_path).replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None
    root = Path(vault_root).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


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
    """A ``data:<mime>;base64,…`` URL for a vault image, or ``None`` when it is
    missing, empty, too big, not an image, or outside the vault.
    An http(s) or data URL passes through unchanged."""
    if not rel_path:
        return None
    if rel_path.startswith(("http://", "https://", "data:")):
        return rel_path
    abs_path = confine(vault_root, rel_path)
    if abs_path is None or not abs_path.is_file():
        return None
    size = abs_path.stat().st_size
    if size == 0 or size > MAX_IMAGE_BYTES:
        return None
    mime, _ = mimetypes.guess_type(str(abs_path))
    if not mime or not mime.startswith("image/"):
        return None
    return f"data:{mime};base64,{base64.b64encode(abs_path.read_bytes()).decode('ascii')}"


def resolve_images(vault_root: str | Path, paths: list[str] | None) -> list[str]:
    """Paths/URLs → ready-to-send URLs; unresolvable items are dropped (compare
    lengths to detect it and tell the user)."""
    return [u for u in (path_to_data_url(vault_root, p) for p in (paths or [])) if u]


# ── Documents ──────────────────────────────────────────────────────


@dataclass
class ExtractedFile:
    path: str
    name: str
    text: str
    truncated: bool
    error: str = ""


def is_extractable_path(path: str) -> bool:
    return bool(path) and Path(path).suffix.lower() in EXTRACTABLE_EXTENSIONS


def extract_file(vault_root: str | Path, rel_path: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> ExtractedFile:
    name = Path(str(rel_path)).name
    abs_path = confine(vault_root, rel_path)
    if abs_path is None:
        return ExtractedFile(rel_path, name, "", False, error="outside the vault")
    if not abs_path.is_file():
        return ExtractedFile(rel_path, name, "", False, error="file not found")
    ext = abs_path.suffix.lower()
    try:
        if ext == ".pdf":
            from emptyos.sdk.pdf import extract_pdf_text

            text = extract_pdf_text(abs_path)
        elif ext == ".docx":
            text = _extract_docx(abs_path)
        elif ext in (".txt", ".md", ".markdown"):
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        else:
            return ExtractedFile(rel_path, name, "", False, error=f"unsupported file type: {ext}")
    except ImportError as e:
        return ExtractedFile(rel_path, name, "", False, error=f"parser missing — pip install required: {e}")
    except Exception as e:
        return ExtractedFile(rel_path, name, "", False, error=f"{type(e).__name__}: {e}")
    truncated = len(text) > max_chars
    return ExtractedFile(rel_path, name, text[:max_chars], truncated)


def _extract_docx(path: Path) -> str:
    try:
        import docx  # type: ignore
    except ImportError as e:
        raise ImportError("python-docx") from e
    doc = docx.Document(str(path))
    lines = [p.text for p in doc.paragraphs if p.text]
    for tbl in doc.tables:  # tables as tab-separated rows so structure survives
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append("\t".join(cells))
    return "\n".join(lines)


def format_block(extracted: ExtractedFile) -> str:
    """One extracted file as a fenced markdown block for the model."""
    if extracted.error:
        return f"[Attached file `{extracted.name}` failed to read: {extracted.error}]"
    suffix = " (truncated)" if extracted.truncated else ""
    return f"[Attached file: {extracted.name}{suffix}]\n```\n{extracted.text}\n```"


# ── Uploads ────────────────────────────────────────────────────────


def store_upload(
    vault_root: str | Path, rel_dir: str, filename: str, data: bytes, *, max_bytes: int
) -> dict:
    """Save uploaded bytes under ``rel_dir`` (vault-relative) with a timestamped,
    sanitised name. ``{path, name, size}`` or ``{error}``."""
    if not data:
        return {"error": "empty file"}
    if len(data) > max_bytes:
        return {"error": f"file too large ({len(data) // (1024 * 1024)}MB > {max_bytes // (1024 * 1024)}MB cap)"}
    if not vault_root:
        return {"error": "no vault configured"}
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", filename or "upload.bin").strip("-.") or "upload.bin"
    rel_path = f"{rel_dir.strip('/')}/{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe}"
    target = confine(vault_root, rel_path)
    if target is None:
        return {"error": "upload directory is outside the vault"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        return {"error": f"write failed: {e}"}
    return {"path": rel_path, "name": filename or safe, "size": len(data)}


# ── One turn's attachments ─────────────────────────────────────────


@dataclass
class TurnAttachments:
    """A message's attachment paths, sorted into what the model receives."""

    image_paths: list[str]   # vault-relative, in order — stored as eos_image refs
    image_urls: list[str]    # data URLs, same order — sent to the provider
    file_blocks: list[str]   # fenced extracted text, appended to the user text
    problems: list[str]      # one line per attachment that could not be used
    names: list[str] = field(default_factory=list)   # what was attached, for the transcript

    @property
    def has_images(self) -> bool:
        return bool(self.image_urls)


def prepare_turn(
    vault_root: str | Path, paths: list[str] | None, *,
    max_chars: int = DEFAULT_MAX_CHARS, max_items: int = 8,
) -> TurnAttachments:
    """Sort attachment paths into images and documents for one chat turn.

    Every path is a vault-relative path, confined to the vault: a URL is
    refused outright, because ``path_to_data_url`` passes one through and the
    provider would then fetch it — an attachment list is not a way to make the
    model reach an arbitrary address. An image that will not load, a document
    that will not extract, an unsupported type and anything past ``max_items``
    are reported in ``problems`` (the caller tells the user) — never dropped in
    silence.
    """
    out = TurnAttachments([], [], [], [])
    items = [str(p or "").strip() for p in (paths or [])]
    items = [p for p in items if p]
    for extra in items[max_items:]:
        out.problems.append(f"{Path(extra).name}: not sent — at most {max_items} attachments per message")
    for p in items[:max_items]:
        name = Path(p).name
        if p.startswith(("http://", "https://", "data:")) or "://" in p:
            out.problems.append(f"{name}: only files in your vault can be attached")
        elif confine(vault_root, p) is None:
            out.problems.append(f"{name}: outside the vault")
        elif is_image_path(p):
            url = path_to_data_url(vault_root, p)
            if url:
                out.image_paths.append(p)
                out.image_urls.append(url)
                out.names.append(name)
            else:
                out.problems.append(f"{name}: image missing, empty or larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        elif is_extractable_path(p):
            ex = extract_file(vault_root, p, max_chars=max_chars)
            if ex.error:
                out.problems.append(f"{name}: {ex.error}")
            else:
                out.file_blocks.append(format_block(ex))
                out.names.append(name)
        else:
            out.problems.append(f"{name}: unsupported file type")
    return out


def provider_reads_images(provider) -> bool:
    """Whether a think provider's model accepts image input.

    A provider may declare ``supports_vision``; otherwise Anthropic models are
    multimodal and an OpenAI-compatible one is judged by its model name (the
    same pattern list its own vision path uses)."""
    declared = getattr(provider, "supports_vision", None)
    if isinstance(declared, bool):
        return declared
    kind = getattr(provider, "kind", "")
    if kind == "anthropic":
        return True
    if kind in ("openai", "json"):
        from emptyos.capabilities.providers.openai_compat import model_supports_vision

        return model_supports_vision(getattr(provider, "model", "") or "")
    return False


# ── Provider-native user content ───────────────────────────────────


def _image_part(kind: str, url: str) -> dict:
    if kind == "anthropic":
        m = _DATA_URL.match(url)
        if m:
            return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "image_url", "image_url": {"url": url}}


def build_user_content(kind: str, text: str, image_urls: list[str] | None = None) -> Any:
    """A user message's ``content`` for provider family ``kind``.

    No images → the plain string (exactly what a text-only turn always sent).
    With images → a part list: OpenAI-compatible (``openai`` / ``json``) gets
    ``image_url`` parts, ``anthropic`` gets ``image`` source blocks.
    """
    urls = [u for u in (image_urls or []) if u]
    if not urls:
        return text
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    parts.extend(_image_part(kind, u) for u in urls)
    return parts


def dehydrate_content(content: Any, image_paths: list[str]) -> Any:
    """Replace the image parts of a just-sent user message with path references
    (``{"type": "eos_image", "path": …}``) for storage. Parts are matched in
    order; a part with no path to name (an external URL) is kept as sent."""
    if not isinstance(content, list) or not image_paths:
        return content
    paths = iter(image_paths)
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
            ref = next(paths, None)
            out.append({"type": EOS_IMAGE, "path": ref} if ref else part)
        else:
            out.append(part)
    return out


#: Marks a stored message whose content came from the user's own files or
#: notes, and the words they typed beside it — read by ``hydrate_messages``
#: when a later turn must not replay that content (CLAUDE.md rule 19).
EOS_VAULT_DERIVED = "eos_vault_derived"
EOS_TYPED = "eos_typed"
#: The cloud provider this message's content was explicitly approved for.
EOS_CLOUD_OK = "eos_cloud_ok"
#: What was attached, by name — so a reopened chat can show it.
EOS_ATTACHED = "eos_attached"

WITHHELD_NOTE = "[content from the user's files withheld: this model is in the cloud and was not approved for it]"


def mark_vault_derived(message: dict, typed: str = "", approved_for: str = "", names=()) -> dict:
    """Tag a message as carrying the user's files or notes.

    ``typed`` is what the user wrote themselves (kept even when the rest is
    withheld); ``approved_for`` names the cloud provider the user approved
    this content for, so a later turn on that same model replays it in full.
    """
    mark = {**message, EOS_VAULT_DERIVED: True}
    if typed:
        mark[EOS_TYPED] = typed
    if approved_for:
        mark[EOS_CLOUD_OK] = approved_for
    if names:
        mark[EOS_ATTACHED] = list(names)
    return mark


#: Image bytes one replayed history may carry (data URLs, so ≈ this × 4/3 on
#: the wire). Past it, older images replay as a note — a chat with eight 12 MB
#: images would otherwise re-encode and re-send ~128 MB on every turn.
MAX_REPLAY_IMAGE_BYTES = 24 * 1024 * 1024

NO_VISION_NOTE = "[image not sent: this model cannot read images]"
TOO_MUCH_NOTE = "[earlier image not re-sent: this conversation's images exceed the per-turn limit]"


def hydrate_messages(
    messages: list[dict], vault_root: str | Path, kind: str, *,
    withhold: bool = False, provider_name: str = "", images: bool = True,
    max_image_bytes: int = MAX_REPLAY_IMAGE_BYTES,
) -> list[dict]:
    """Turn stored ``eos_image`` references back into ``kind``'s image parts.

    An image that can no longer be read (moved, deleted, now outside the vault)
    becomes a short text note rather than vanishing, so the model knows an
    image was there. Every ``eos_*`` block and top-level ``eos_*`` key is
    removed — providers reject unknown fields.

    ``withhold`` (a cloud provider on a turn the user did not approve) replays
    a vault-derived message — the user's question, the reply that quoted their
    notes, and the tool results in between — as a note, keeping only words the
    user typed themselves. A message approved for this same provider
    (``eos_cloud_ok``) is replayed in full: that approval was given for this
    content going to this model.

    ``images=False`` (a model that cannot read images) and the
    ``max_image_bytes`` budget each replace an image with a note rather than
    sending something the turn cannot carry.
    """
    out = []
    budget = max_image_bytes
    for m in messages:
        if withhold and m.get(EOS_VAULT_DERIVED) and m.get(EOS_CLOUD_OK) != (provider_name or None):
            typed = str(m.get(EOS_TYPED) or "").strip()
            out.append({**_strip_eos(m),
                        "content": f"{typed}\n\n{WITHHELD_NOTE}" if typed else WITHHELD_NOTE})
            continue
        m = _strip_eos(m)
        content = m.get("content")
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == EOS_IMAGE for p in content):
            parts = []
            for p in content:
                if not (isinstance(p, dict) and p.get("type") == EOS_IMAGE):
                    parts.append(p)
                    continue
                if not images:
                    parts.append({"type": "text", "text": NO_VISION_NOTE})
                    continue
                url = path_to_data_url(vault_root, p.get("path") or "")
                if not url:
                    parts.append({"type": "text", "text": f"[image {p.get('path')!r} is no longer available]"})
                elif len(url) > budget:
                    parts.append({"type": "text", "text": TOO_MUCH_NOTE})
                else:
                    budget -= len(url)
                    parts.append(_image_part(kind, url))
            m["content"] = parts
        out.append(m)
    return out


def _strip_eos(m: dict) -> dict:
    return {k: v for k, v in m.items() if not str(k).startswith("eos_")}
