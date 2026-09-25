"""viz — single-shot LLM->HTML core + persistence.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The non-streaming generation pipeline: shape->system prompt, the think call, size/truncation validation, vault persistence (scene.html + record.md), and the public `generate` verb consumed by VizProvider + api_generate. Owns the vault path helpers..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._build_examples_block (examples) for few-shot injection.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .shared import (
    PRESETS,
    VIZ_3D_SCENE_PRESET,
    VIZ_BASE_SYSTEM,
    _artifact_title,
    _extract_html,
    _looks_like_html,
    _looks_truncated,
    _new_id,
    _normalize_game_2d_html,
    _now_iso,
    _shape_max_tokens,
    _shape_min_ability,
)

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
#   _salvage_enabled    = _generation._salvage_enabled
#   _truncation_salvage = _generation._truncation_salvage
#   _persist            = _generation._persist
#   _think_html_stream  = _generation._think_html_stream
#   generate            = _generation.generate
#   save_artifact       = _generation.save_artifact
#   _snapshot_version   = _generation._snapshot_version
#   _version_ring_size  = _generation._version_ring_size
#   _versions_dir       = _generation._versions_dir
#   list_versions       = _generation.list_versions
#   restore_version     = _generation.restore_version
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


# ─── Version ring ────────────────────────────────────────────────────
# `history` records prompts, never renders, so before this an iterate or an
# element-edit that made an artifact worse was unrecoverable — the previous
# HTML had already been overwritten in place. The ring keeps the last N
# renders beside the record so a bad turn is undoable. Named as the graduation
# path in `.claude/rules/artifact-element-edit.md`, and it is what makes the
# `[provides.timeline]` this app declares tell the truth: a version list IS the
# artifact's past.

VERSION_RING_DEFAULT = 10


def _versions_dir(self, rid: str) -> Path:
    return self._record_dir(rid) / "versions"


def _version_ring_size(self) -> int:
    """How many prior renders to keep. 0 disables snapshotting entirely."""
    try:
        n = int(self.setting_or_config("viz.version_ring", VERSION_RING_DEFAULT))
    except (TypeError, ValueError):
        return VERSION_RING_DEFAULT
    return max(0, n)


def _snapshot_version(self, rid: str, prompt: str = "") -> int | None:
    """Copy the CURRENT scene.html into the ring. Returns its version number.

    Called before an overwrite, so the snapshot is the render being replaced —
    never the new one. Returns ``None`` when there is nothing to preserve (a
    first generation) or the ring is disabled.

    Best-effort by design: a failure here must not lose the user the render
    they actually asked for, so it is swallowed and logged rather than raised.
    Losing an undo step is bad; failing the generation because the undo step
    could not be written is worse.
    """
    keep = self._version_ring_size()
    if keep <= 0:
        return None
    current = self._record_dir(rid) / "scene.html"
    if not current.exists():
        return None
    try:
        vdir = self._versions_dir(rid)
        vdir.mkdir(parents=True, exist_ok=True)
        n = max((_version_num(p) for p in vdir.iterdir() if _version_num(p)), default=0) + 1
        slot = vdir / str(n)
        slot.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, slot / "scene.html")
        fm = self.vault_get_properties(self._rel_record(rid)) or {}
        (slot / "meta.json").write_text(
            json.dumps({
                "n": n,
                "ts": fm.get("updated") or _now_iso(),
                "prompt": str(prompt or fm.get("prompt") or ""),
                "size_kb": round(current.stat().st_size / 1024, 1),
            }),
            encoding="utf-8",
        )
        # Prune oldest beyond the ring. Numbers keep climbing, so a version
        # number is a stable identity for as long as it exists.
        nums = sorted(x for x in (_version_num(p) for p in vdir.iterdir()) if x)
        for old in nums[:-keep] if len(nums) > keep else []:
            shutil.rmtree(vdir / str(old), ignore_errors=True)
        return n
    except Exception as e:  # noqa: BLE001 — see docstring
        self.log(f"viz: version snapshot failed for {rid}: {e}", level="warning")
        return None


def _version_num(p: Path) -> int | None:
    """Version number of a ring directory, or None if it isn't one."""
    try:
        return int(p.name) if p.is_dir() and p.name.isdigit() else None
    except OSError:
        return None


def list_versions(self, rid: str) -> list[dict]:
    """Ring contents, newest first. Empty when nothing has been overwritten."""
    vdir = self._versions_dir(rid)
    if not vdir.exists():
        return []
    out: list[dict] = []
    for p in vdir.iterdir():
        n = _version_num(p)
        if not n or not (p / "scene.html").exists():
            continue
        meta: dict = {}
        try:
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a ring entry without readable meta
            pass          # is still restorable; the html is what matters.
        out.append({
            "n": n,
            "ts": meta.get("ts", ""),
            "prompt": meta.get("prompt", ""),
            "size_kb": meta.get("size_kb", 0),
        })
    return sorted(out, key=lambda v: v["n"], reverse=True)


def restore_version(self, rid: str, n: int) -> dict:
    """Put ring version ``n`` back as the live scene.html.

    The restore snapshots the render it replaces first, so restoring is itself
    undoable and cannot become the destructive operation this ring exists to
    prevent.
    """
    slot = self._versions_dir(rid) / str(int(n))
    src = slot / "scene.html"
    if not src.exists():
        return {"ok": False, "error": f"no version {n} for '{rid}'"}
    rec_rel = self._rel_record(rid)
    fm = self.vault_get_properties(rec_rel)
    if not fm:
        return {"ok": False, "error": f"no artifact with id '{rid}'"}

    saved_as = self._snapshot_version(rid, prompt=f"before restoring v{n}")
    shutil.copy2(src, self._record_dir(rid) / "scene.html")

    now = _now_iso()
    history = list(fm.get("history") or [])
    history.append({"ts": now, "prompt": f"restored v{n}"})
    self.vault_update(rec_rel, {
        "updated": now,
        "history": history,
        "size_kb": round(src.stat().st_size / 1024, 1),
    })
    return {"ok": True, "id": rid, "restored": int(n), "saved_as": saved_as}


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


def _salvage_enabled(self) -> bool:
    """Dark flag ``apps.viz.feature.truncation-salvage.enabled`` (default off).
    Off → a truncated artifact is discarded exactly as before."""
    return bool(self.app_config("feature.truncation-salvage.enabled", False))


def _truncation_salvage(self, html: str) -> str | None:
    """If salvage is on and `html` is a *truncated* artifact (not not-HTML, not
    oversize), return a valid banner-wrapped document built from the partial;
    else None. The output-token ceiling should degrade to 'save what completed +
    say what was cut' (no silent cap), never silently discard the whole thing."""
    if not self._salvage_enabled():
        return None
    if not _looks_like_html(html):
        return None
    truncated, why = _looks_truncated(html)
    if not truncated:
        return None
    from emptyos.sdk.html_artifact import salvage_truncated_html
    kb = max(1, round(len(html.encode("utf-8")) / 1024))
    return salvage_truncated_html(
        html,
        banner_note=(f"This artifact was truncated at the model's output limit "
                     f"(~{kb} KB generated) — {why}. It is incomplete; regenerate "
                     f"with a shorter or simpler brief, or split it into parts."),
    )


# Standard note surfaced to the caller when a truncated artifact was salvaged.
_SALVAGE_NOTE = "Saved a partial artifact — output was truncated at the model's limit; the page carries a banner saying so."


async def _persist(
    self,
    rid: str,
    html: str,
    prompt: str,
    shape: str,
    *,
    is_update: bool,
    patterns: list[str] | None = None,
    source: str = "",
) -> dict:
    """Write scene.html + record.md, return metadata dict.

    `patterns` are the KB `kind: pattern` slugs whose code actually reached
    the system prompt (see `_build_examples_block`); `source` is the origin
    the caller wants recorded — a vault-relative note path, or an app id for
    a programmatic caller. Both land in frontmatter keys that
    ``vault-graph`` walks, which is what stops an artifact being an isolated
    node in the graph. Both optional: callers that know neither are unchanged.
    """
    if shape == "game-2d":
        html = _normalize_game_2d_html(html)
    record_dir = self._record_dir(rid)
    record_dir.mkdir(parents=True, exist_ok=True)

    # Stamp data-eos-el anchors so the element-edit loop can resolve a clicked
    # element to an exact source span — only when the dark flag is on AND the
    # shape is DOM-structured (canvas shapes have no element to anchor). Idempotent
    # + a no-op when off, so saved artifacts stay byte-identical pre-feature.
    if self._edit_enabled() and self._shape_supports_edit(shape):
        from emptyos.sdk.html_anchors import inject_anchors
        html = inject_anchors(html, attr="data-eos-el", prefix="e")

    # Preserve the render being replaced before it is overwritten. A no-op on a
    # first generation (nothing to preserve) and when the ring is disabled.
    self._snapshot_version(rid, prompt=prompt)

    html_path = record_dir / "scene.html"
    html_path.write_text(html, encoding="utf-8")

    record_path = record_dir / "record.md"
    now = _now_iso()
    existing: dict = {}
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

    # `lifecycle` is deliberately left to vault_create_note's folder inference
    # (30_Resources/ -> living), which is correct here: an iterate rewrites THIS
    # record in place and grows `history`, so the note genuinely keeps changing.
    # It is not a frozen snapshot the way an AI report note is.
    fm = {
        "tags": ["viz"],
        "title": _artifact_title(display_prompt, fallback=f"Viz artifact {rid}"),
        "viz_id": rid,
        "shape": shape,
        "prompt": display_prompt,
        "author": "ai",
        "created": created,
        "updated": now,
        "size_kb": round(len(html.encode("utf-8")) / 1024, 1),
        "history": history,
    }
    # Provenance. These are `vault-graph` FRONTMATTER_REF_FIELDS, so writing
    # them is what earns the artifact its edges. An update carries neither, so
    # fall back to what the record already holds rather than erasing it.
    related = [p for p in (patterns or []) if p] or (existing.get("related") or [])
    if related:
        fm["related"] = related
    origin = (source or "").strip() or str(existing.get("source") or "").strip()
    if origin:
        fm["source"] = origin

    title = fm["title"]
    body = f"# {title}\n\n**Original brief:** {display_prompt}\n\n**Latest change:** {prompt}\n\n[Open scene.html]({self._rel_html(rid)})\n"
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


async def save_artifact(
    self,
    content: str,
    *,
    title: str = "",
    shape: str = "",
    source: str = "",
    rid: str = "",
) -> dict:
    """Persist HTML somebody ELSE wrote, through viz's own storage.

    ``generate`` is viz asking a model for an artifact; this is viz being
    handed one. The chat home's CreateArtifact tool is the first caller: the
    model writes the page inside its tool call, so a second generation pass
    would only re-derive what already exists.

    Everything downstream is deliberately identical to ``generate`` — the same
    ``_reject_reason`` validation, the same ``_persist``, so the artifact gets
    a record.md, the vault-graph ``source`` link, the element-edit anchors and
    (the reason this is not a plain file write) the **version ring**: passing
    the ``rid`` of an existing artifact snapshots the render being replaced,
    so "make it blue instead" is undoable rather than destructive.

    ``shape`` is optional and descriptive here — nothing is generated from it,
    so an unknown value is refused rather than silently recorded.
    """
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "content is required"}
    shape = (shape or "").strip() or self.app_config("default_shape", "3d-scene")
    if shape not in PRESETS:
        return {"ok": False, "error": f"unknown shape '{shape}' (have: {sorted(PRESETS)})"}
    reason = self._reject_reason(content)
    if reason:
        return {"ok": False, "error": reason}

    title = (title or "").strip() or "Untitled artifact"
    rid = (rid or "").strip()
    is_update = False
    if rid:
        # The id becomes a directory name; a caller-supplied segment never
        # reaches the filesystem unchecked (the in-band form, not the raising
        # one — this returns an error body, so a refused id must not 500).
        from emptyos.sdk.utils import path_segment_error

        bad = path_segment_error(rid, "artifact id")
        if bad:
            return {"ok": False, "error": bad}
        is_update = self._record_dir(rid).exists()
        if is_update:
            # An UNCHANGED re-save is not a revision. A model that repeats a
            # tool call — measured: 25 byte-identical calls in one turn, one
            # every ~5s until the loop's iteration cap stopped it — would
            # otherwise spend the whole version ring on copies of the current
            # render and prune away the one render the user might want back.
            # The ring exists to make a revision undoable; a duplicate is the
            # one thing it must not store.
            live = self._record_dir(rid) / "scene.html"
            try:
                unchanged = live.read_text(encoding="utf-8") == content
            except OSError:
                unchanged = False
            if unchanged:
                fm = self.vault_get_properties(self._rel_record(rid)) or {}
                return {
                    "ok": True, "id": rid, "revised": False, "unchanged": True,
                    "title": fm.get("title") or title, "shape": fm.get("shape") or shape,
                    "created": fm.get("created", ""), "updated": fm.get("updated", ""),
                    "html_path": self._rel_html(rid), "record_path": self._rel_record(rid),
                }
        if not is_update:
            # A caller naming an id we have never seen is working from a stale
            # reference; minting that id would resurrect a deleted artifact at
            # a path the caller only half-remembers.
            return {"ok": False, "error": "no such artifact"}
    else:
        rid = _new_id()

    meta = await self._persist(
        rid, content, title, shape, is_update=is_update, source=source
    )
    if is_update:
        # _persist reads its `prompt` argument as a BRIEF and deliberately keeps
        # the first one as the artifact's identity ("make it blue" must not
        # rename the artifact). Here the argument is a TITLE, and a caller that
        # sends a new one means it — so without this the record kept the old
        # title while the caller was told the new one had been saved, and the
        # chat panel, the viz list and the vault note all disagreed.
        self.vault_update(self._rel_record(rid), {"title": title})
    await self.emit("viz:updated" if is_update else "viz:created", {"id": rid, "shape": shape})
    # Literals AFTER the spread, not before. `revised` avoids today's collision
    # (meta carries an `updated` TIMESTAMP, which shadowed a False flag of that
    # name and made every first save announce itself as an update); putting the
    # literals last means the next key _persist grows cannot do it again.
    # `title` is restated for the same reason meta's `prompt` cannot be trusted
    # here: on an update that field is the ORIGINAL brief, not what was saved.
    return {**meta, "ok": True, "revised": is_update, "title": title}


async def generate(
    self,
    prompt: str,
    *,
    shape: str | None = None,
    examples: list[str] | None = None,
    source: str = "",
) -> dict:
    """Single-shot generation. Returns same dict shape as api_generate.

    `examples` names KB `kind: pattern` notes whose code fences are injected
    into the system prompt as few-shot. `source` is an optional origin the
    caller wants recorded on the artifact — a vault-relative note path, or an
    app id for a programmatic caller — so the artifact points back at whatever
    produced it instead of only being pointed at.
    """
    prompt = (prompt or "").strip()
    shape = shape or self.app_config("default_shape", "3d-scene")
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    if shape not in PRESETS:
        return {"ok": False, "error": f"unknown shape '{shape}' (have: {sorted(PRESETS)})"}

    system = self._system_for(shape)
    examples_block, used_patterns = await self._build_examples_block(examples or [], shape)
    if examples_block:
        system = system + examples_block

    html = await self._think_html(system, prompt, min_ability=_shape_min_ability(shape), max_tokens=_shape_max_tokens(shape))
    reason = self._reject_reason(html)
    truncated = False
    if reason:
        salvaged = self._truncation_salvage(html)
        if salvaged is None:
            return {"ok": False, "error": reason}
        html, truncated = salvaged, True

    rid = _new_id()
    meta = await self._persist(
        rid, html, prompt, shape, is_update=False, patterns=used_patterns, source=source
    )
    await self.emit("viz:created", {"id": rid, "shape": shape})
    if truncated:
        return {"ok": True, "truncated": True, "note": _SALVAGE_NOTE, **meta}
    return {"ok": True, **meta}
