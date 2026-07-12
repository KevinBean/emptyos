"""Aura — vault-backed memory: durable user facts + recent-journal context.

Memory notes live at ``{vault}/30_Resources/EmptyOS/voice-assistant/memory/``
as one markdown file per entry, tagged ``aura-memory``. Mirrors the
Claude Code auto-memory contract (types: user / feedback / project /
reference; one file per entry with frontmatter; index-by-vault-query
rather than a separate MEMORY.md).

Why vault, not data/: memory IS user knowledge — preferences, facts,
hard-won lessons. Belongs in the markdown vault so the user can read,
edit, and sync across machines. Per Kevin's plan-mode choice 2026-05-18.

Three surfaces:
  1. Memory CRUD (M1): memory_save / memory_load_all / memory_delete
  2. Voice intents (M2): voice_remember / voice_forget / voice_recall
  3. Context contributors (M3): context_memory / context_recent_journal

Extracted from `app.py` to keep the spine focused. Module-level functions
are bound onto `VoiceAssistantApp` in `app.py` per
`.claude/rules/multi-module-apps.md`.

Reaches into other modules: `self.vault_*` (BaseApp), `self.vault_root`,
`self.vault_dir`. Do not import from `.app` (cycle).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _memory_dir              = _memory._memory_dir
#   _memory_rel_dir          = _memory._memory_rel_dir
#   _memory_slug_from        = _memory._memory_slug_from        # @staticmethod
#   _memory_cache_get        = _memory._memory_cache_get
#   memory_save              = _memory.memory_save
#   memory_load_all          = _memory.memory_load_all
#   memory_delete            = _memory.memory_delete
#   voice_remember           = _memory.voice_remember
#   voice_forget             = _memory.voice_forget
#   voice_recall             = _memory.voice_recall
#   context_memory           = _memory.context_memory
#   context_recent_journal   = _memory.context_recent_journal
#   api_memory_save          = _memory.api_memory_save
#   api_memory_list          = _memory.api_memory_list
#   api_memory_delete        = _memory.api_memory_delete
#   api_debug_context        = _memory.api_debug_context
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


VALID_KINDS = {"user", "feedback", "project", "reference"}
MEMORY_CONTEXT_BUDGET = 600           # chars of the memory readout block
JOURNAL_CONTEXT_BUDGET = 500          # chars of the recent-journal block
JOURNAL_LOOKBACK_DAYS = 3
MEMORY_CACHE_TTL_S = 60               # mirror apps/assistant's TTL pattern
JOURNAL_DIR = "50_Journal"            # canonical PARA folder for daily notes


def _memory_dir(self) -> Path:
    """Absolute path to the memory directory in the vault."""
    return self.vault_dir / "memory"


def _memory_rel_dir(self) -> str:
    """Vault-rel path prefix (forward slashes, no trailing slash)."""
    rel = self.vault_rel(self.vault_dir) or f"30_Resources/EmptyOS/{self.manifest.id}"
    return rel + "/memory"


_SLUG_KEEP = re.compile(r"[^a-z0-9-]+")


def _memory_slug_from(text: str) -> str:
    """Derive a short kebab-case slug from free text. ≤8 words, ≤60 chars."""
    if not text:
        return ""
    words = [w for w in re.split(r"\s+", text.strip().lower())[:8] if w]
    raw = "-".join(words)
    raw = _SLUG_KEEP.sub("-", raw).strip("-")
    return raw[:60] or ""


def _memory_cache_get(self) -> list[dict] | None:
    """60s TTL cache for the memory readout, mirrors apps/assistant pattern."""
    ts = getattr(self, "_memory_cache_ts", 0.0)
    if not ts or (time.time() - ts) > MEMORY_CACHE_TTL_S:
        return None
    return getattr(self, "_memory_cache", None)


def _memory_cache_set(self, value: list[dict] | None) -> None:
    self._memory_cache = value
    self._memory_cache_ts = time.time()


def _memory_cache_invalidate(self) -> None:
    self._memory_cache = None
    self._memory_cache_ts = 0.0


def memory_save(
    self,
    *,
    kind: str = "user",
    body: str = "",
    name: str | None = None,
    description: str | None = None,
    companion: str = "",
) -> dict:
    """Create a new memory note in the vault. Idempotent on slug — if a
    note with the derived slug already exists, returns an error rather
    than overwriting (the user can `aura.forget` then re-remember).

    When companion-memory-scope is on and ``companion`` is set, the note is
    namespaced to that companion (``companion:`` frontmatter); otherwise it
    is a universal memory visible to every persona.
    """
    body = (body or "").strip()
    if not body:
        return {"error": "body required"}
    kind = (kind or "user").strip().lower()
    if kind not in VALID_KINDS:
        kind = "user"

    slug = _memory_slug_from(name or description or body)
    if not slug:
        return {"error": "could not derive slug"}

    rel_path = f"{_memory_rel_dir(self)}/{slug}.md"
    abs_path = self.vault_root / rel_path
    if abs_path.exists():
        return {"error": "memory note already exists", "slug": slug, "path": rel_path}

    now_iso = datetime.now(timezone.utc).isoformat()
    fm = {
        "tags": ["aura-memory"],
        "kind": kind,
        "name": slug,
        "description": (description or body[:80]).strip(),
        "created": now_iso,
        "updated": now_iso,
    }
    companion = (companion or "").strip()
    if companion and _companion_scope_on(self):
        fm["companion"] = companion
    body_text = body if body.endswith("\n") else body + "\n"
    self.vault_create_note(rel_path, fm, body_text)
    _memory_cache_invalidate(self)
    return {"slug": slug, "path": rel_path, "kind": kind}


def memory_load_all(self) -> list[dict]:
    """All memory notes, newest first. Each row: {slug, kind, description,
    body, created, updated, path}. Empty list when no vault mounted or
    no notes exist.
    """
    cached = _memory_cache_get(self)
    if cached is not None:
        return cached

    rows = self.vault_query(tags=["aura-memory"])
    out: list[dict] = []
    vault_root = getattr(self, "vault_root", None)
    for r in rows or []:
        props = r.get("properties") or {}
        rel = r.get("path") or ""
        # Defensive against index drift: a deleted file may linger in the
        # in-memory index until the watcher catches up. Drop rows whose
        # backing file is gone so memory_delete is observable immediately.
        if vault_root:
            try:
                if not (vault_root / rel).exists():
                    continue
            except Exception:
                pass
        raw = self.vault_read_at(rel)
        # Strip frontmatter (first --- block).
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            body = raw[end + 4:].lstrip("\n") if end != -1 else raw
        else:
            body = raw
        out.append({
            "slug": props.get("name") or Path(rel).stem,
            "kind": (props.get("kind") or "user").lower(),
            "description": props.get("description") or "",
            "body": body.strip(),
            "created": props.get("created") or "",
            "updated": props.get("updated") or "",
            "companion": props.get("companion") or "",
            "path": rel,
        })
    out.sort(key=lambda x: x.get("created") or "", reverse=True)
    _memory_cache_set(self, out)
    return out


def memory_delete(self, slug: str) -> bool:
    """Remove a memory note by slug. Returns True if a file was removed."""
    slug = (slug or "").strip()
    if not slug:
        return False
    abs_path = _memory_dir(self) / f"{slug}.md"
    if not abs_path.exists():
        return False
    try:
        abs_path.unlink()
    except Exception:
        return False
    # Synchronously evict from the VaultIndex so the next vault_query
    # doesn't return the stale row before the filesystem watcher catches
    # up. vault_force_index re-indexes against current disk state — when
    # the file is gone (just unlinked above), it pops the entry.
    try:
        rel = self.vault_rel(abs_path)
        if rel:
            self.vault_force_index(rel)
    except Exception:
        pass
    _memory_cache_invalidate(self)
    return True


# ── Ranked recall (Phase 1 borrow — moeru-ai/airi memory scorer) ───────

def _ranked_recall_on(self) -> bool:
    """feature.ranked-recall.enabled — dark default. When off, recall stays
    on the legacy fuzzy-substring + dump-all path (byte-for-byte)."""
    return bool(self.app_config("feature.ranked-recall.enabled", False))


def _memory_embed_text(r: dict) -> str:
    """Embed text for a memory row: description + body."""
    return f"{r.get('description', '')} {r.get('body', '')}".strip()


# Recall blend tuned for Aura memory: similarity DOMINATES — we want the most
# relevant fact, not the most recent — with recency + salience only as
# tie-breakers. min_sim keeps a merely-recent but semantically-unrelated
# memory from being pulled in by the recency term. (airi's defaults
# (1.2, 0.3, 0.1) let recency outrank weak-similarity items, which is wrong
# for personal-fact recall.)
RECALL_WEIGHTS = (1.6, 0.2, 0.1)
RECALL_MIN_SIM = 0.30


def _companion_scope_on(self) -> bool:
    """feature.companion-memory-scope.enabled — dark default. When off,
    every memory is universal (today's behaviour) and no `companion:` field
    is written."""
    return bool(self.app_config("feature.companion-memory-scope.enabled", False))


def _scope_memories(self, rows: list[dict]) -> list[dict]:
    """Filter memory rows by the active companion (airi soul-container borrow).

    Flag off → rows unchanged. Active companion C → C's own memories plus
    universal (no `companion`) ones. Aura mode (no active companion) →
    universal only. The active companion is stashed per-turn by the chat
    pipeline (`self._active_companion`)."""
    if not _companion_scope_on(self):
        return rows
    active = (getattr(self, "_active_companion", "") or "").strip()
    out = []
    for r in rows:
        comp = (r.get("companion") or "").strip()
        if not comp:
            out.append(r)               # universal — visible to all personas
        elif active and comp == active:
            out.append(r)               # this companion's own memory
    return out


# ── Voice intents (M2) ────────────────────────────────────────────────

async def voice_remember(
    self,
    body: str = "",
    kind: str = "user",
    description: str = "",
) -> dict:
    """Save a memory note. Wraps memory_save."""
    if not (body or "").strip():
        return {"say": "What should I remember?"}
    result = memory_save(
        self, kind=kind, body=body, description=description or None,
        companion=(getattr(self, "_active_companion", "") or ""),
    )
    if "error" in result:
        if result["error"] == "memory note already exists":
            return {"say": "I already remember that."}
        return {"say": f"Couldn't save that — {result['error']}."}
    # No `link` field: there is no dedicated memory list page; users can
    # browse memories via the `voice_recall` intent, which renders a
    # memory-list card in the voice transcript.
    return {"say": "Got it. I'll remember that."}


def _fuzzy_match(rows: list[dict], query: str) -> list[dict]:
    """Substring match against slug + description + body. Case-insensitive."""
    q = (query or "").strip().lower()
    if not q:
        return list(rows)
    out: list[dict] = []
    for r in rows:
        hay = " ".join([
            r.get("slug", ""),
            r.get("description", ""),
            r.get("body", ""),
        ]).lower()
        if q in hay:
            out.append(r)
    return out


async def voice_forget(self, query: str = "") -> dict:
    """Find a memory note by fuzzy text match and delete it. If 0 or >1
    match, ask for clarification rather than guessing."""
    q = (query or "").strip()
    if not q:
        return {"say": "What should I forget?"}
    rows = memory_load_all(self)
    matches = _fuzzy_match(rows, q)
    if not matches:
        return {"say": f"I don't have anything matching '{q}'."}
    if len(matches) > 1:
        previews = "; ".join((m["description"] or m["body"])[:40] for m in matches[:3])
        return {"say": f"More than one match — {previews}. Be more specific?"}
    target = matches[0]
    ok = memory_delete(self, target["slug"])
    if not ok:
        return {"say": "Couldn't remove that note."}
    desc = (target["description"] or target["body"] or target["slug"])[:60]
    # No `link`: no dedicated memory list page exists; users can browse
    # remaining memories via `voice_recall` (renders memory-list card).
    return {"say": f"Forgotten: {desc}."}


async def voice_recall(self, query: str = "") -> dict:
    """List remembered facts, optionally filtered by fuzzy match. Always
    returns a card; spoken summary varies by count."""
    rows = _scope_memories(self, memory_load_all(self))
    if query and _ranked_recall_on(self) and self.embeddings_available:
        # Blended similarity + recency + salience (airi scorer). Falls back
        # to fuzzy below if recall surfaces nothing above threshold.
        filtered = await self.recall(
            query, items=rows, text_fn=_memory_embed_text, top_k=10,
            weights=RECALL_WEIGHTS, min_sim=RECALL_MIN_SIM,
        )
        if not filtered:
            filtered = _fuzzy_match(rows, query)
    else:
        filtered = _fuzzy_match(rows, query) if query else rows
    if not filtered:
        if query:
            return {"say": f"Nothing remembered matches '{query}'."}
        return {"say": "I don't remember anything specific about you yet."}
    n = len(filtered)
    if n == 1:
        say = f"One thing: {(filtered[0]['description'] or filtered[0]['body'])[:80]}."
    else:
        say = f"{n} things. Top: {(filtered[0]['description'] or filtered[0]['body'])[:60]}."
    card_data = [
        {
            "kind": r["kind"],
            "body": r["body"] or r["description"],
            "created": (r.get("created") or "")[:10],
        }
        for r in filtered[:10]
    ]
    return {
        "say": say,
        "card": {"renderer": "memory-list", "title": "Remembered", "data": card_data},
    }


# ── Context contributors (M3) ─────────────────────────────────────────

async def context_memory(self) -> dict | None:
    """Inject the memory readout into Aura's per-turn context, at
    priority 45 (Critical tier, just above open tasks).

    Cap at MEMORY_CONTEXT_BUDGET chars; truncate oldest entries past that.
    Returns None when there's nothing to inject so the tier loop drops it
    cleanly.
    """
    rows = _scope_memories(self, memory_load_all(self))
    if not rows:
        return None
    # When ranked recall is on and we have a query for this turn (stashed by
    # the chat pipeline), order memories by relevance+recency+salience so the
    # fixed char budget spends on what matters this turn, not just the newest.
    query = (getattr(self, "_recall_query", "") or "").strip()
    if query and _ranked_recall_on(self) and self.embeddings_available:
        ranked = await self.recall(
            query, items=rows, text_fn=_memory_embed_text, top_k=20,
            weights=RECALL_WEIGHTS, min_sim=RECALL_MIN_SIM,
        )
        if ranked:
            rows = ranked
    lines = ["What I remember about you:"]
    used = len(lines[0])
    for r in rows:
        kind = r.get("kind") or "user"
        text = (r.get("description") or r.get("body") or "").strip()
        if not text:
            continue
        text = text.replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        line = f"• [{kind}] {text}"
        if used + len(line) + 1 > MEMORY_CONTEXT_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) == 1:
        return None
    return {"summary": "\n".join(lines), "priority": 45}


async def context_recent_journal(self) -> dict | None:
    """Inject a short summary of the last ~3 daily journal entries at
    priority 140 (Situational, just above projects).

    Reads `{vault}/50_Journal/{year}/{YYYY-MM-DD}.md`. Skips silently
    when no journal directory exists or no recent entries are found.
    """
    if not (self.vault_root / "50_Journal").exists():
        return None

    today = datetime.now(timezone.utc).date()
    from datetime import timedelta
    found: list[tuple[str, str]] = []
    for delta in range(JOURNAL_LOOKBACK_DAYS + 5):  # extra lookback for sparse journals
        d = today - timedelta(days=delta)
        rel = f"{JOURNAL_DIR}/{d.year}/{d.isoformat()}.md"
        raw = self.vault_read_at(rel)
        if raw:
            # Strip frontmatter and clip first non-empty paragraph.
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end != -1:
                    raw = raw[end + 4:].lstrip("\n")
            cleaned = " ".join(
                ln.strip() for ln in raw.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            )
            if cleaned:
                snippet = cleaned[:120].rstrip()
                if len(cleaned) > 120:
                    snippet += "..."
                found.append((d.isoformat(), snippet))
        if len(found) >= JOURNAL_LOOKBACK_DAYS:
            break

    if not found:
        return None

    lines = ["Recent journal:"]
    used = len(lines[0])
    for iso, snippet in found:
        line = f"• {iso}: {snippet}"
        if used + len(line) + 1 > JOURNAL_CONTEXT_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) == 1:
        return None
    return {"summary": "\n".join(lines), "priority": 140}


# ── HTTP routes (M1 verification, plus optional admin) ────────────────

@web_route("POST", "/api/memory/save")
async def api_memory_save(self, request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return memory_save(
        self,
        kind=body.get("kind") or "user",
        body=body.get("body") or "",
        name=body.get("name"),
        description=body.get("description"),
    )


@web_route("GET", "/api/memory")
async def api_memory_list(self, request):
    return {"memory": memory_load_all(self)}


@web_route("POST", "/api/memory/{slug}/delete")
async def api_memory_delete(self, request):
    slug = request.path_params["slug"]
    return {"ok": memory_delete(self, slug)}


@web_route("GET", "/debug/context")
async def api_debug_context(self, request):
    """Dump the current `_build_context()` output for inspection. Useful
    while testing the memory + recent-journal contributors."""
    companion = request.query_params.get("companion") or None
    text = await self._build_context(companion)
    return {"companion": companion, "context": text, "chars": len(text)}
