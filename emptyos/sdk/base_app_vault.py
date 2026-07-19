"""BaseApp vault family — vault paths, note I/O, index queries, projects.

Extracted from base_app.py to keep the BaseApp spine atomic (P4 Atomic,
CLAUDE.md rule 4). Source split only — every function here is re-bound onto
``BaseApp`` in base_app.py's class body, so the public API (``self.vault_*``)
is unchanged. Owns: vault-map/config resolution, raw vault file I/O, the
VaultIndex layer (query / frontmatter / sections / body), flat-frontmatter
JSON encoding, attested-memory trust, and vault-backed project CRUD. Also
home to ``INTEREST_SKIP_TAGS`` / ``_interest_folder_key`` — base_app.py
imports them back (its ``scoped_retrieve`` + external tests use them).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (spine helpers via ``self``).
Do not import from ``.base_app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import UTC
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk.utils import now_iso

if TYPE_CHECKING:
    from .base_app import BaseApp  # noqa: F401 — for type hints only


# ─── Bind to BaseApp class as ────────────────────────────────────────
#   vault_config                = _vault.vault_config
#   vault_config_path           = _vault.vault_config_path
#   vault_root                  = _vault.vault_root # @property
#   vault_dir                   = _vault.vault_dir # @cached_property
#   vault_path                  = _vault.vault_path
#   vault_rel                   = _vault.vault_rel
#   vault_rel_if_exists         = _vault.vault_rel_if_exists
#   vault_read                  = _vault.vault_read
#   vault_write                 = _vault.vault_write
#   vault_list                  = _vault.vault_list
#   vault_read_at               = _vault.vault_read_at
#   vault_write_at              = _vault.vault_write_at
#   vault_query                 = _vault.vault_query
#   vault_interest_profile      = _vault.vault_interest_profile
#   _infer_lifecycle            = _vault._infer_lifecycle # @staticmethod
#   vault_update                = _vault.vault_update
#   vault_encode_json           = _vault.vault_encode_json # @staticmethod
#   vault_decode_json           = _vault.vault_decode_json # @staticmethod
#   vault_create_note           = _vault.vault_create_note
#   vault_append_section        = _vault.vault_append_section
#   vault_set_section           = _vault.vault_set_section
#   vault_get_properties        = _vault.vault_get_properties
#   vault_tags                  = _vault.vault_tags
#   vault_trust                 = _vault.vault_trust
#   vault_confirm               = _vault.vault_confirm
#   vault_force_index           = _vault.vault_force_index
#   vault_sections              = _vault.vault_sections
#   vault_read_section          = _vault.vault_read_section
#   vault_read_body             = _vault.vault_read_body
#   vault_set_body              = _vault.vault_set_body
#   vault_reconcile             = _vault.vault_reconcile
#   vault_enrich                = _vault.vault_enrich
#   vault_project_list          = _vault.vault_project_list
#   vault_project_create        = _vault.vault_project_create
#   vault_project_update        = _vault.vault_project_update
#   vault_project_delete        = _vault.vault_project_delete
#   vault_project_get           = _vault.vault_project_get
#   vault_project_read_sidecar  = _vault.vault_project_read_sidecar
#   vault_project_write_sidecar = _vault.vault_project_write_sidecar
# Adding a new method here? Add a matching binding line in base_app.py.
# ─────────────────────────────────────────────────────────────────────


# Generic / structural tags that say nothing about a person's interests —
# dropped by ``vault_interest_profile``. Extend per consumer via skip_tags=.
INTEREST_SKIP_TAGS = frozenset({
    "daily", "weekly", "monthly", "yearly", "journal", "kb", "clause", "reference",
    "case", "concept", "formula", "lesson", "pattern", "doc", "moc", "note",
    "inbox", "project", "area", "archive", "person", "people", "task", "todo",
    "web-clip", "song", "draft", "untitled",
})


def _interest_folder_key(folder: str) -> str:
    """`10_Projects` → `projects`, `20_Areas` → `areas`. Strips a leading
    numeric/PARA prefix so the profile keys read naturally."""
    base = (folder or "").replace("\\", "/").split("/")[-1]
    i = 0
    while i < len(base) and (base[i].isdigit() or base[i] in "_-"):
        i += 1
    return (base[i:] or base).lower()


def vault_config(self, key: str, default: str = "") -> str:
    """Get a vault path from the vault map. Settings override map file.

    Usage: path = self.vault_config("people_dir", "30_Resources/People")
    """
    app_id = self.manifest.id
    # Settings override (highest priority)
    settings = self.kernel.services.get_optional("settings")
    if settings:
        override = settings.get(f"{app_id}.vault_path.{key}")
        if override:
            return str(override)
    # Vault map
    return self.kernel.vault_map.get(app_id, key, default)


def vault_config_path(self, key: str, default: str = "") -> Path | None:
    """Get an absolute vault path from the vault map."""
    rel = self.vault_config(key, default)
    if not rel:
        return None
    vault = self.kernel.config.notes_path
    if not vault:
        return None
    return vault / rel


# --- Vault Storage ---
# Apps can persist human-readable data to the vault (markdown files).
# Unlike state (JSON in data/), vault files are visible in vault apps,
# synced across devices, and editable by the user.


@property
def vault_root(self) -> Path:
    """The configured vault root, or Path(".") if no vault is mounted.

    Use when the app needs to read/write anywhere in the vault (not just its
    own subdir). For per-app storage use ``vault_dir`` / ``vault_write``.
    """
    return self.kernel.config.notes_path or Path(".")


@cached_property
def vault_dir(self) -> Path:
    """This app's directory in the vault. Created on first write.

    Reads vault base from kernel config. Subfolder prefix configurable
    via settings key 'vault.app_prefix' (default: '30_Resources/EmptyOS').
    """
    vault = self.vault_root
    prefix = "30_Resources/EmptyOS"
    settings = self.kernel.services.get_optional("settings")
    if settings:
        custom = settings.get("vault.app_prefix")
        if custom:
            prefix = str(custom)
    return vault / prefix / self.manifest.id


def vault_path(self, filename: str) -> Path:
    """Get a path inside this app's vault directory."""
    return self.vault_dir / filename


def vault_rel(self, p) -> str:
    """Return ``p`` as a forward-slash, vault-root-relative string.

    Vault-side sibling of :meth:`repo_rel`. Fails soft: returns ``""``
    when ``p`` lies outside the vault (or can't be resolved), so callers
    with a custom fallback compose as ``self.vault_rel(p) or fallback``
    — a successful result is never empty.

    Use when:
        - Handing a path to the VaultIndex (``vault_query`` /
          ``vault_update`` / ``vault_get_properties`` take rel paths).
        - Returning ``_vault_path`` from a detail endpoint for the
          4D-timeline contract, or implementing a
          ``[provides.timeline]`` ``entity_source`` method.

    Not for:
        - Traversal *guards* — this formats, it doesn't refuse. Keep an
          explicit ``resolve().relative_to()`` check (or 400 response)
          where escaping the root must be rejected.
    """
    try:
        return str(
            Path(p).resolve().relative_to(self.vault_root.resolve())
        ).replace("\\", "/")
    except (TypeError, ValueError, OSError):
        return ""


def vault_rel_if_exists(self, rel: str) -> str:
    """Return ``rel`` unchanged if it names an existing note under the
    vault root, else ``""``.

    The existence-guard half of a ``[provides.timeline]`` ``entity_source``
    method: an app resolves an entity id to a candidate vault-relative path
    its own way, then returns it only when the note actually exists — so
    the 4D-timeline drawer never resolves a dead entity. Pairs with
    :meth:`vault_rel` (which *formats* a path; this one *guards* it).

    Use when:
        - Implementing ``entity_source`` and you already hold a rel path.

    Not for:
        - Resolution itself — each app maps an id → path differently
          (``_rel_record`` / ``_find_note`` / ``_find_project_file`` …).
        - Traversal guards — this checks existence, not root containment.
    """
    if not rel:
        return ""
    return rel if (self.vault_root / rel).exists() else ""


def vault_read(self, filename: str, default: str = "") -> str:
    """Read a file from this app's vault directory."""
    p = self.vault_path(filename)
    if p.exists():
        return p.read_text(encoding="utf-8", errors="ignore")
    return default


def vault_write(self, filename: str, content: str):
    """Write a file to this app's vault directory."""
    p = self.vault_path(filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def vault_list(self, pattern: str = "*.md") -> list[Path]:
    """List files in this app's vault directory."""
    if not self.vault_dir.exists():
        return []
    return sorted(self.vault_dir.glob(pattern))


def vault_read_at(self, rel_path: str, default: str = "") -> str:
    """Read a file at a vault-root-relative path.

    Use when the file lives outside the app's own vault_dir (e.g. a
    path resolved from ``vault_config()`` that already includes the
    full ``30_Resources/EmptyOS/<app>/...`` prefix).
    """
    p = self.vault_root / rel_path
    if p.exists():
        return p.read_text(encoding="utf-8", errors="ignore")
    return default


def vault_write_at(self, rel_path: str, content: str) -> None:
    """Write a file at a vault-root-relative path. Creates parents."""
    p = self.vault_root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# --- Vault Index (query + mutate vault notes via platform index) ---


def vault_query(
    self, tags: list[str] | None = None, folder: str | None = None, **properties
) -> list[dict]:
    """Query vault notes by tags and/or frontmatter properties.

    Returns list of {path, name, folder, ext, size, properties, tags}.
    Uses the platform VaultIndex (in-memory, fast).
    Falls back to empty list if VaultIndex is unavailable.
    """
    vi = self.kernel.services.get_optional("vault_index")
    if not vi:
        return []
    rows = vi.find(tags=tags, folder=folder, **properties)
    if not rows:
        self._record_demand(
            kind="vault_query",
            query=json.dumps({"tags": tags, "folder": folder, **properties}, default=str)[:500],
            result="empty",
        )
    return rows


def vault_interest_profile(
    self,
    *,
    folders: tuple[str, ...] = ("10_Projects", "20_Areas"),
    max_tags: int = 25,
    skip_tags: set[str] | frozenset[str] | None = None,
) -> dict:
    """A compact, low-sensitivity 'what this user cares about' profile.

    Returns folder-entry names (e.g. project + area titles) keyed by a
    normalized folder label, plus the most-used note tags::

        {"projects": [...], "areas": [...], "tags": [[tag, count], ...]}

    (keys derive from ``folders`` via :func:`_interest_folder_key`, so
    ``10_Projects`` → ``projects``.)

    **Metadata only — never note prose.** Designed for deriving topic /
    focus personalization (news lens, study suggestions, companion
    framing). This builder does NOT call a model and does NOT send
    anything anywhere — the caller decides what, if any, of this to put in
    a prompt (CLAUDE.md rule 19: explicit, per-request, minimal). Pair it
    with the propose→preview→confirm paradigm when it drives a setting.

    First consumers: ``daily-brief`` (focus lens) + ``hub`` (Aura next-move
    grounding). Latent: the user-intent recommender, learn topic-suggestion.
    """
    root = self.vault_root
    skip = {t.lower() for t in (INTEREST_SKIP_TAGS if skip_tags is None else skip_tags)}

    def subdirs(folder: str) -> list[str]:
        p = root / folder
        if not p.exists() or not p.is_dir():
            return []
        try:
            names = [
                d.name for d in p.iterdir()
                if d.is_dir() and not d.name.startswith((".", "_"))
            ]
        except OSError:
            return []
        return sorted(names)[:30]

    out: dict = {}
    for folder in folders:
        out[_interest_folder_key(folder)] = subdirs(folder)

    tags: list[list] = []
    vi = self.kernel.services.get_optional("vault_index")
    if vi is not None and hasattr(vi, "tag_counts"):
        try:
            tc = vi.tag_counts() or {}
        except Exception:  # noqa: BLE001 — fail-soft, profile is best-effort
            tc = {}
        ranked = sorted(
            ((t, c) for t, c in tc.items() if str(t).lower() not in skip),
            key=lambda x: -x[1],
        )
        tags = [[t, c] for t, c in ranked[:max_tags]]
    out["tags"] = tags
    return out


@staticmethod
def _infer_lifecycle(rel_path: str) -> str | None:
    """Folder-heuristic lifecycle classifier (.claude/rules/time-dimension.md).

    Returns living | snapshot | archived, or None when no confident inference.
    """
    p = (rel_path or "").replace("\\", "/").lstrip("/")
    if p.startswith("40_Archive/"):
        return "archived"
    if p.startswith("50_Journal/"):
        return "snapshot"
    if p.startswith(("00_Inbox/", "10_Projects/", "20_Areas/", "30_Resources/")):
        return "living"
    return None


def vault_update(self, rel_path: str, properties: dict):
    """Update frontmatter properties in a vault note and re-index.

    rel_path: relative to vault root (e.g. "20_Areas/Career/Job-Applications/foo/_app.md")
    properties: dict of key-value pairs to update in frontmatter

    Touch-time lifecycle backfill: if the note has no `lifecycle:` field yet
    and this update isn't setting one, infer from folder and inject. New
    labels accrete on notes as they're naturally edited — no big-bang
    migration. See `.claude/rules/time-dimension.md`.
    """
    vi = self.kernel.services.get_optional("vault_index")
    if not vi:
        return
    if "lifecycle" not in properties:
        try:
            existing = vi.get_properties(rel_path) or {}
        except Exception:
            existing = {}
        if not existing.get("lifecycle"):
            inferred = self._infer_lifecycle(rel_path)
            if inferred:
                properties = {**properties, "lifecycle": inferred}
    vi.update_properties(rel_path, properties)


# ── flat-frontmatter JSON encoding ──
# Vault frontmatter is flat YAML — nested structures (list of dicts,
# etc.) must be JSON-encoded into a single string field. The `@json `
# sentinel prefix keeps `vault_index._parse_fm` from misreading the
# value as an inline YAML array, which would silently corrupt every
# nested dict on read. Two consumers today: `apps/kb/` (Document
# paragraphs) and the workflows app's `templates.py` (chain steps).


@staticmethod
def vault_encode_json(value) -> str:
    """Serialize a nested structure for storage in a single frontmatter field."""
    return "@json " + json.dumps(value if value is not None else [])


@staticmethod
def vault_decode_json(raw, default=None):
    """Decode the inverse of `vault_encode_json`. Tolerates legacy/empty/malformed input."""
    if default is None:
        default = []
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw  # already-decoded list/dict — pass through
    payload = raw.strip()
    if payload.startswith("@json "):
        payload = payload[len("@json "):].strip()
    if not payload:
        return default
    try:
        return json.loads(payload)
    except Exception:
        return default


def vault_create_note(self, rel_path: str, frontmatter: dict, body: str = ""):
    """Create a new vault note with frontmatter + body and index it.

    rel_path: relative to vault root
    frontmatter: dict of YAML frontmatter key-value pairs
    body: markdown body content
    """
    if not frontmatter.get("lifecycle"):
        inferred = self._infer_lifecycle(rel_path)
        if inferred:
            frontmatter = {**frontmatter, "lifecycle": inferred}
    vi = self.kernel.services.get_optional("vault_index")
    if vi:
        vi.create_note(rel_path, frontmatter, body)
    else:
        # Direct write fallback — used when the vault_index service is absent.
        # It needs its OWN containment check: the guard added to
        # VaultIndex.create_note doesn't run on this branch, so without this
        # a traversal would still escape whenever the index is unavailable.
        vault = self.kernel.config.notes_path
        if vault:
            import os

            from emptyos.runtime.vault_index import _serialize_fm

            root = os.path.normpath(str(vault))
            joined = os.path.normpath(os.path.join(root, str(rel_path)))
            if joined != root and not joined.startswith(root + os.sep):
                raise ValueError(f"path escapes the vault root: {rel_path!r}")
            abs_path = Path(joined)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(_serialize_fm(frontmatter) + "\n\n" + body, encoding="utf-8")


def vault_append_section(self, rel_path: str, section: str, text: str):
    """Append text to a ## section in a vault note and re-index."""
    vi = self.kernel.services.get_optional("vault_index")
    if vi:
        vi.append_to_section(rel_path, section, text)


def vault_set_section(self, rel_path: str, section: str, text: str) -> None:
    """Replace (or insert) a ## section's content, preserving frontmatter.

    Set-semantics sibling of ``vault_append_section``: append adds to the end
    of a section, this overwrites it. Inserts the section at the end of the
    body when it doesn't exist yet. Re-indexes after the write so the change
    is immediately queryable.
    """
    import re as _re

    vault = self.kernel.config.notes_path
    if not vault:
        return
    abs_path = vault / rel_path
    if not abs_path.exists():
        return
    try:
        full = abs_path.read_text(encoding="utf-8")
    except Exception:
        return
    fm, body = "", full
    if full.startswith("---"):
        parts = full.split("---", 2)
        if len(parts) == 3:
            fm, body = "---" + parts[1] + "---", parts[2]
    header = f"## {section}"
    block = f"{header}\n\n{(text or '').strip()}\n"
    pat = _re.compile(r"(?ms)^" + _re.escape(header) + r"[ \t]*\n.*?(?=^## |\Z)")
    if pat.search(body):
        body = pat.sub(block + "\n", body, count=1)
    else:
        body = body.rstrip() + "\n\n" + block
    out = (fm + "\n\n" + body.lstrip("\n")) if fm else body.lstrip("\n")
    abs_path.write_text(out, encoding="utf-8")
    self.vault_force_index(rel_path)


def vault_get_properties(self, rel_path: str) -> dict:
    """Get all frontmatter properties for a vault note (from index, fast)."""
    vi = self.kernel.services.get_optional("vault_index")
    if vi:
        return vi.get_properties(rel_path)
    return {}


def vault_tags(self, rel_path: str) -> list[str]:
    """Tags for a vault note (from index). Separate from vault_get_properties
    because the index pops `tags` out of frontmatter into its own field.
    Empty list when the index or note is unavailable."""
    vi = self.kernel.services.get_optional("vault_index")
    try:
        return list(vi.get_tags(rel_path)) if vi else []
    except Exception:
        return []


# --- Attested Memory (derived trust over provenance; docs/MEMORY.md) ---


def vault_trust(self, rel_path: str, *, stale_days: int | None = None) -> dict:
    """Derived trust verdict for a vault note — pure read, no write, no model.

    Reads indexed frontmatter only (``docs/MEMORY.md`` invariant #7) and
    returns ``{path, level, attestation, reasons, age_days, version}`` where
    ``level`` is ``trusted | tentative | suspect | retired``. Trust is
    *derived*, never stored — recomputed every call (§4).
    """
    from datetime import datetime

    from emptyos.sdk.attested_memory import DEFAULT_STALE_DAYS, classify_trust

    props = self.vault_get_properties(rel_path) or {}
    v = classify_trust(
        props,
        now=datetime.now(UTC).date(),
        stale_days=DEFAULT_STALE_DAYS if stale_days is None else stale_days,
    )
    return {"path": rel_path, **v.to_dict()}


def vault_confirm(self, rel_path: str) -> dict:
    """Mark a claim still-true: bump ``last_verified`` to today.

    The **confirm** operation from ``docs/MEMORY.md`` §5 — an explicit,
    user-initiated, reversible single-field frontmatter write (the prior
    ``last_verified`` is recoverable; nothing is overwritten or deleted, so
    invariant #1 holds). This is NOT the read-only audit path. A confirmed
    AI-inferred claim reads ``trusted`` afterward via ``last_verified``,
    with ``attestation: inferred`` preserved (origin is never erased).
    Returns the fresh trust verdict.
    """
    from datetime import datetime

    self.vault_update(rel_path, {"last_verified": datetime.now(UTC).date().isoformat()})
    return self.vault_trust(rel_path)


def vault_force_index(self, rel_path: str) -> None:
    """Synchronously refresh the in-memory index for one vault path.

    Use after a direct filesystem write or delete to make the change
    immediately queryable, without waiting for the watcher to pump.
    Indexes the file if it exists; evicts the entry if it doesn't.
    No-op when no VaultIndex service is mounted.
    """
    vi = self.kernel.services.get_optional("vault_index")
    if vi and hasattr(vi, "index_file"):
        try:
            vi.index_file(rel_path)
        except Exception:
            pass


def vault_sections(self, rel_path: str) -> list[str]:
    """List ## section names in a vault note (from index, instant)."""
    vi = self.kernel.services.get_optional("vault_index")
    if vi:
        entry = vi._files.get(rel_path)
        return list(entry.get("sections", [])) if entry else []
    return []


def vault_read_section(self, rel_path: str, section: str) -> str:
    """Read content of a specific ## section from a vault note.

    Returns the text between ## section and the next ## (or end of file).
    Reads from disk (sections are not cached in index).
    """
    vault = self.kernel.config.notes_path
    if not vault:
        return ""
    abs_path = vault / rel_path
    if not abs_path.exists():
        return ""
    try:
        content = abs_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    header = f"## {section}"
    lines = content.split("\n")
    collecting = False
    result = []
    for line in lines:
        if line.strip() == header:
            collecting = True
            continue
        if collecting:
            if line.startswith("## ") and not line.startswith("### "):
                break
            result.append(line)
    # Strip leading/trailing blank lines
    text = "\n".join(result).strip()
    return text


def vault_read_body(self, rel_path: str) -> str:
    """Read everything after frontmatter from a vault note."""
    vault = self.kernel.config.notes_path
    if not vault:
        return ""
    abs_path = vault / rel_path
    if not abs_path.exists():
        return ""
    try:
        content = abs_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            return content[end + 3 :].strip()
    return content.strip()


async def vault_set_body(self, rel_path: str, body: str) -> dict:
    """Replace a vault note's body (everything after frontmatter), preserving
    the frontmatter block verbatim. The write-side inverse of
    ``vault_read_body``.

    Serialized per-file via the kernel-wide ``note_lock`` so a concurrent
    write can't clobber it (CLAUDE.md § Development Gotchas — vault
    read-modify-write races). Returns ``{ok, path}`` or ``{error}``; never
    raises. ``rel_path`` is vault-relative.
    """
    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "no vault configured"}
    abs_path = vault / rel_path
    if not abs_path.exists():
        return {"error": "not found"}
    async with self.note_lock(rel_path):
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception:
            return {"error": "read failed"}
        fm = ""
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                after = end + 3
                if content[after : after + 1] == "\n":
                    after += 1
                fm = content[:after]
        if fm and not fm.endswith("\n"):
            fm += "\n"
        try:
            await self.write(str(abs_path), fm + body)
        except Exception:
            return {"error": "write failed"}
    return {"ok": True, "path": rel_path}


# --- Vault Data Contracts ---


def vault_reconcile(
    self,
    folder: str,
    expected_tags: list[str] | None = None,
    expected_fields: list[str] | None = None,
) -> dict:
    """Check vault notes against expected structure. Read-only — reports gaps."""
    vi = self.kernel.services.get("vault_index")
    if not vi:
        return {"total": 0, "compliant": 0, "gaps": [], "folder": folder}
    return vi.reconcile(folder, expected_tags, expected_fields)


def vault_enrich(
    self, rel_path: str, add_tags: list[str] | None = None, defaults: dict | None = None
) -> bool:
    """Add missing tags/defaults to a vault note. Safe — never overwrites."""
    vi = self.kernel.services.get("vault_index")
    if not vi:
        return False
    return vi.enrich(rel_path, add_tags, defaults)


# --- Vault-backed projects (generic CRUD over tag-marked notes) ---
# Pattern: an app's "projects" are vault notes with a known tag, a
# frontmatter shape the app owns, and a settable-fields whitelist.
# These helpers handle the plumbing; the app supplies tag, path,
# extra creation fields, and event names.


def vault_project_list(
    self,
    *,
    tag: str,
    list_fields: list[str] | None = None,
    defaults: dict | None = None,
) -> list[dict]:
    """List non-archived vault notes carrying ``tag``.

    Each row carries id/name/created/updated/_path plus any extra
    frontmatter keys named in ``list_fields``. ``defaults`` fills in
    missing keys (use for fields the app guarantees a default for —
    e.g. ``frequency_hz=50.0``). Sorted by ``updated`` desc.
    """
    notes = self.vault_query(tags=[tag])
    defaults = defaults or {}
    out: list[dict] = []
    for n in notes:
        fm = n.get("properties") or {}
        if fm.get("archived"):
            continue
        row: dict = {
            "id": fm.get("id") or Path(n["path"]).stem,
            "name": fm.get("name") or fm.get("id") or Path(n["path"]).stem,
            "created": fm.get("created"),
            "updated": fm.get("updated"),
            "_path": n["path"],
        }
        for f in (list_fields or []):
            if f in fm:
                row[f] = fm[f]
            elif f in defaults:
                row[f] = defaults[f]
        out.append(row)
    return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)


async def vault_project_create(
    self,
    *,
    project_id: str,
    name: str,
    tag: str,
    path: str,
    extra_fm: dict | None = None,
    body: str | None = None,
    event_name: str | None = None,
) -> dict:
    """Create a vault-backed project note. Returns ``{"ok", "id", "path"}``
    or ``{"error": ...}`` if a note already exists at ``path``."""
    if self.vault_get_properties(path):
        return {"error": f"project {project_id} already exists"}
    fm: dict = {
        "id": project_id,
        "name": name,
        "tags": [tag],
        "created": now_iso(),
        "updated": now_iso(),
    }
    if extra_fm:
        fm.update(extra_fm)
    body_md = body if body is not None else f"# {name}\n\n## Notes\n\n## Tasks\n\n"
    self.vault_create_note(path, fm, body_md)
    if event_name:
        await self.emit(event_name, {"id": project_id, "name": name})
    return {"ok": True, "id": project_id, "path": path}


async def vault_project_update(
    self,
    *,
    project_id: str,
    path: str,
    fields: dict,
    settable: set,
    event_name: str | None = None,
) -> dict:
    """Update whitelisted frontmatter on a project note. Returns
    ``{"ok": True}`` or ``{"error": ...}``."""
    if not self.vault_get_properties(path):
        return {"error": "project not found"}
    bad = [k for k in fields if k not in settable]
    if bad:
        return {"error": f"fields not settable: {bad}"}
    update = dict(fields)
    update["updated"] = now_iso()
    self.vault_update(path, update)
    if event_name:
        await self.emit(event_name, {
            "id": project_id, "fields": list(fields.keys()),
        })
    return {"ok": True}


async def vault_project_delete(
    self,
    *,
    project_id: str,
    path: str,
    event_name: str | None = None,
) -> dict:
    """Soft-delete a project note (sets ``archived: true`` in frontmatter).
    Real deletion is a vault-tool concern, not the app's."""
    if not self.vault_get_properties(path):
        return {"ok": False, "error": "project not found"}
    self.vault_update(path, {"archived": True, "updated": now_iso()})
    if event_name:
        await self.emit(event_name, {"id": project_id})
    return {"ok": True}


def vault_project_get(self, path: str) -> dict | None:
    """Read a project note's frontmatter, with ``_path`` injected.

    Returns the merged frontmatter dict or ``None`` if no note exists at
    ``path``. Callers typically wrap as ``return {"project": fm}`` or
    ``return {"error": "project not found"}``.
    """
    fm = self.vault_get_properties(path)
    if not fm:
        return None
    return {**fm, "_path": path}


def vault_project_read_sidecar(
    self,
    *,
    project_path: str,
    sidecar_path: str,
    key: str = "readings",
) -> dict:
    """Read a sidecar JSON file next to a project note.

    Sidecars hold structured data (lists of readings, geometry, …) that
    vault frontmatter can't carry — frontmatter is flat-only. Returns
    ``{"ok": True, <key>: [...]}`` on success, ``{"error": ...}`` if the
    project is missing or the sidecar can't be parsed. Empty/missing
    sidecar returns ``{"ok": True, <key>: []}`` — the absence of a
    sidecar is not an error.
    """
    if not self.vault_get_properties(project_path):
        return {"error": "project not found"}
    raw = self.vault_read_at(sidecar_path)
    if not raw:
        return {"ok": True, key: []}
    try:
        data = json.loads(raw)
    except ValueError as e:
        return {"error": f"cannot read sidecar: {e}"}
    items = data if isinstance(data, list) else (data.get(key) or [])
    return {"ok": True, key: items}


async def vault_project_write_sidecar(
    self,
    *,
    project_id: str,
    project_path: str,
    sidecar_path: str,
    items: list,
    key: str = "readings",
    event_name: str | None = None,
) -> dict:
    """Replace a project's sidecar JSON. Caller pre-cleans ``items``.

    Writes ``{<key>: items}`` as indented JSON, bumps the project's
    ``updated`` timestamp, and (optionally) emits ``event_name`` with
    ``{"id": project_id, "n": len(items)}``. Returns ``{"ok": True,
    "n": ...}`` or ``{"error": "project not found"}``. Row-shape
    validation lives in the caller — sidecar schemas vary per app.
    """
    if not self.vault_get_properties(project_path):
        return {"error": "project not found"}
    self.vault_write_at(sidecar_path, json.dumps({key: items}, indent=2))
    self.vault_update(project_path, {"updated": now_iso()})
    if event_name:
        await self.emit(event_name, {"id": project_id, "n": len(items)})
    return {"ok": True, "n": len(items)}
