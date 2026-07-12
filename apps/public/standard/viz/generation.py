"""viz — single-shot LLM->HTML core + persistence.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The non-streaming generation pipeline: shape->system prompt, the think call, size/truncation validation, vault persistence (scene.html + record.md), and the public `generate` verb consumed by VizProvider + api_generate. Owns the vault path helpers..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._build_examples_block (examples) for few-shot injection.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from .shared import VIZ_BASE_SYSTEM, VIZ_3D_SCENE_PRESET, PRESETS, _shape_min_ability, _shape_max_tokens, _extract_html, _looks_like_html, _looks_truncated, _now_iso, _new_id
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VizApp  # noqa: F401 — for type hints only


# ─── Bind to VizApp class as ────────────────────────────────
#   _outputs_root       = _generation._outputs_root
#   _record_dir         = _generation._record_dir
#   _rel_html           = _generation._rel_html
#   _rel_record         = _generation._rel_record
#   _system_for         = _generation._system_for
#   _think_html         = _generation._think_html
#   _check_size         = _generation._check_size
#   _reject_reason      = _generation._reject_reason
#   _persist            = _generation._persist
#   _think_html_stream  = _generation._think_html_stream
#   generate            = _generation.generate
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _outputs_root(self) -> Path:
    rel = self.vault_config("path", "30_Resources/EmptyOS/viz") + "/outputs"
    return self.vault_root / rel


def _record_dir(self, rid: str) -> Path:
    return self._outputs_root() / rid


def _rel_html(self, rid: str) -> str:
    base = self.vault_config("path", "30_Resources/EmptyOS/viz")
    return f"{base}/outputs/{rid}/scene.html"


def _rel_record(self, rid: str) -> str:
    base = self.vault_config("path", "30_Resources/EmptyOS/viz")
    return f"{base}/outputs/{rid}/record.md"


def _system_for(self, shape: str) -> str:
    preset = PRESETS.get(shape, VIZ_3D_SCENE_PRESET)
    return VIZ_BASE_SYSTEM + "\n\n" + preset


async def _think_html(self, system: str, user: str, *, min_ability: str | None = None, max_tokens: int = 8192) -> str:
    """One think call, returns cleaned HTML string.

    max_tokens defaults to 8192 (2× the SDK default, enough for ~24 KB
    HTML — typical viz artifact is 12-18 KB), but callers pass
    `_shape_max_tokens(shape)` so big shapes (anim-explainer, 3d-scene:
    16384 per SHAPE_META) don't truncate — the streaming path already
    did this; the non-streaming path silently clipped them at 8192.
    Truncation guard (_looks_truncated) is the safety net.

    Caveat carried over from the old 8192 cap: a 16384-token completion
    on a slow non-streaming cloud provider can outrun the provider's
    aiohttp timeout (openai_compat: ClientTimeout(total=self.timeout),
    ~30-60s; the streaming path gets 3× plus progressive bytes). If big
    shapes start timing out instead of truncating, raise the provider's
    `timeout` in emptyos.toml — don't lower the shape budget back.
    """
    domain = self.app_config("think_domain", "code")
    raw = await self.think(
        user,
        system=system,
        domain=domain,
        temperature=0.5,  # mid-range — not parsing, not pure creative
        max_tokens=max_tokens,
        min_ability=min_ability,  # route to a sufficiently-able provider
    )
    text = raw if isinstance(raw, str) else str(raw)
    html = _extract_html(text)
    return html


def _check_size(self, html: str) -> tuple[bool, str]:
    max_kb = int(self.app_config("max_html_kb", 256) or 256)
    size_kb = len(html.encode("utf-8")) / 1024
    if size_kb > max_kb:
        return False, f"HTML size {size_kb:.1f} KB exceeds limit {max_kb} KB"
    return True, ""


def _reject_reason(self, html: str) -> str:
    """Return "" if `html` is a saveable artifact, else a human error reason.
    Shared validate tail for both iterate paths (api_iterate + the no-agent
    fallback _iterate_via_think_stream)."""
    if not _looks_like_html(html):
        return "LLM output does not look like HTML"
    truncated, why = _looks_truncated(html)
    if truncated:
        return f"LLM output truncated — refusing to save. {why}. Try a shorter brief, or regenerate (don't iterate) for large files."
    ok, why = self._check_size(html)
    if not ok:
        return why
    return ""


async def _persist(self, rid: str, html: str, prompt: str, shape: str, *, is_update: bool) -> dict:
    """Write scene.html + record.md, return metadata dict."""
    record_dir = self._record_dir(rid)
    record_dir.mkdir(parents=True, exist_ok=True)

    # Stamp data-eos-el anchors so the element-edit loop can resolve a clicked
    # element to an exact source span — only when the dark flag is on AND the
    # shape is DOM-structured (canvas shapes have no element to anchor). Idempotent
    # + a no-op when off, so saved artifacts stay byte-identical pre-feature.
    if self._edit_enabled() and self._shape_supports_edit(shape):
        from emptyos.sdk.html_anchors import inject_anchors
        html = inject_anchors(html, attr="data-eos-el", prefix="e")

    html_path = record_dir / "scene.html"
    html_path.write_text(html, encoding="utf-8")

    record_path = record_dir / "record.md"
    now = _now_iso()
    if is_update and record_path.exists():
        existing = self.vault_get_properties(self._rel_record(rid)) or {}
        created = existing.get("created", now)
        history = existing.get("history", []) or []
        history.append({"ts": now, "prompt": prompt})
        # Preserve the original brief as the artifact's identity;
        # iteration prompts (change requests) only land in history.
        display_prompt = existing.get("prompt") or prompt
    else:
        created = now
        history = [{"ts": now, "prompt": prompt}]
        display_prompt = prompt

    fm = {
        "tags": ["viz"],
        "viz_id": rid,
        "shape": shape,
        "prompt": display_prompt,
        "created": created,
        "updated": now,
        "size_kb": round(len(html.encode("utf-8")) / 1024, 1),
        "history": history,
    }
    body = f"# Viz artifact `{rid}`\n\n**Original brief:** {display_prompt}\n\n**Latest change:** {prompt}\n\n[Open scene.html]({self._rel_html(rid)})\n"
    self.vault_create_note(self._rel_record(rid), fm, body)

    return {
        "id": rid,
        "shape": shape,
        "prompt": display_prompt,
        "created": created,
        "updated": now,
        "record_dir": str(record_dir),
        "html_path": self._rel_html(rid),
        "record_path": self._rel_record(rid),
        "size_kb": fm["size_kb"],
    }


async def _think_html_stream(self, system: str, user: str, *, min_ability: str | None = None, max_tokens: int = 8192):
    """Streaming sibling of `_think_html`. Yields raw text chunks as the
    LLM produces them; caller accumulates + strips fences at the end.

    `max_tokens` defaults to 8192 but callers pass `_shape_max_tokens(shape)`
    so the intricate shapes (3D/animation/games/decks) get a bigger budget.
    Safe to exceed the non-stream 8192 ceiling here: streaming keeps the socket
    alive (chunks flow continuously), so a longer emission can't trip the 60s
    one-shot response timeout that pins the non-stream `_think_html` path.
    """
    domain = self.app_config("think_domain", "code")
    async for chunk in self.think_stream(
        user,
        system=system,
        domain=domain,
        temperature=0.5,
        max_tokens=max_tokens,
        min_ability=min_ability,
    ):
        text = chunk.get("text") or ""
        done = bool(chunk.get("done"))
        if text:
            yield text, done
        if done:
            break


async def generate(
    self,
    prompt: str,
    *,
    shape: str | None = None,
    examples: list[str] | None = None,
) -> dict:
    """Single-shot generation. Returns same dict shape as api_generate.

    `examples` is reserved for the KB-injection mechanism (next session) —
    accepted now so the public signature is stable before consumers wire
    up. Today it's a no-op: any names passed are ignored. When the KB
    pattern reader lands, each name will resolve to a vault path and the
    note's code fences will be appended to the system prompt as few-shot.
    """
    prompt = (prompt or "").strip()
    shape = shape or self.app_config("default_shape", "3d-scene")
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    if shape not in PRESETS:
        return {"ok": False, "error": f"unknown shape '{shape}' (have: {sorted(PRESETS)})"}

    system = self._system_for(shape)
    examples_block = await self._build_examples_block(examples or [], shape)
    if examples_block:
        system = system + examples_block

    html = await self._think_html(system, prompt, min_ability=_shape_min_ability(shape), max_tokens=_shape_max_tokens(shape))
    reason = self._reject_reason(html)
    if reason:
        return {"ok": False, "error": reason}

    rid = _new_id()
    meta = await self._persist(rid, html, prompt, shape, is_update=False)
    await self.emit("viz:created", {"id": rid, "shape": shape})
    return {"ok": True, **meta}
