"""Prompt registry — discoverable defaults + per-machine text overrides.

Spec: ``.claude/rules/prompt-management.md``.

EmptyOS keeps LLM prompts as named UPPERCASE constants next to the code that
parses their output (CLAUDE.md rule 12) — that stays true. What was missing is
any way to *see* the prompts the system runs on, or to tune one's wording on a
specific machine without editing code. This module adds that as a thin layer:

- The constant remains the shipped default, authored and reviewed in git.
- An adopted ``prompts.py`` registers its constants at import time::

      from emptyos.sdk.prompt_registry import declare_prompts

      SESSION_ARCHIVE_SYSTEM = \"\"\"...\"\"\"
      CLASSIFY_SYSTEM = \"\"\"...\"\"\"

      PROMPTS = declare_prompts(
          "agent",
          session_archive_system=SESSION_ARCHIVE_SYSTEM,
          classify_system=CLASSIFY_SYSTEM,
      )

  and call sites switch ``system=CLASSIFY_SYSTEM`` to
  ``system=PROMPTS.classify_system``.
- User overrides live in ``data/prompts/overrides.json`` (per-machine,
  gitignored, edited via the ``/prompts`` app or ``eos prompt``). Resolution
  checks the override first and falls back to the constant.

Fail-open is the contract everywhere: no overrides file, a corrupt file, an
unregistered key, or a placeholder mismatch all resolve to the code default —
an override can degrade to "no effect" but can never break a call site.

Prefix-cache discipline (``.claude/rules/prompt-prefix-cache.md``): resolution
is cheap (one ``stat`` on a cached file) but multi-turn loops must still
resolve once *before* the loop and reuse the string, so the system prefix
stays byte-stable within a run.

Pure module — no kernel import, unit-testable without a daemon
(``tests/test_sdk_prompt_registry.py``). The kernel calls :func:`configure`
at boot; standalone consumers (CLI, tests) pass ``path=`` explicitly.
"""

from __future__ import annotations

import json
import os
import re
import string
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OVERRIDES_SCHEMA_VERSION = 1

# ── module state ─────────────────────────────────────────────────────────
_REGISTRY: dict[str, "PromptEntry"] = {}
_DATA_DIR: Path | None = None
# (mtime_ns, parsed overrides dict) for the configured path; per-path caching
# is deliberately not attempted — explicit ``path=`` callers re-read.
_CACHE: tuple[int, dict] | None = None
_CORRUPT: str | None = None  # last JSON-parse error, surfaced by sweep()


@dataclass(frozen=True)
class PromptEntry:
    """One registered prompt: an app-namespaced name + its code default."""

    app_id: str
    name: str  # snake_case, e.g. "session_archive_system"
    default: str
    placeholders: frozenset[str] | None  # None = not parseable as a template

    @property
    def key(self) -> str:
        return f"{self.app_id}.{self.name}"

    @property
    def constant(self) -> str:
        """The UPPERCASE source-constant name this entry mirrors."""
        return self.name.upper()


def configure(data_dir: Path | str) -> None:
    """Point the registry at the runtime data dir (kernel boot). Idempotent."""
    global _DATA_DIR, _CACHE
    _DATA_DIR = Path(data_dir)
    _CACHE = None


def overrides_path(data_dir: Path | str | None = None) -> Path:
    """``<data>/prompts/overrides.json`` for the given (or configured) dir."""
    base = Path(data_dir) if data_dir is not None else _DATA_DIR
    if base is None:
        raise RuntimeError("prompt_registry is not configured; pass data_dir")
    return base / "prompts" / "overrides.json"


def extract_placeholders(text: str) -> frozenset[str] | None:
    """Named ``{placeholder}`` fields in a prompt, or ``None`` when the text
    isn't parseable as a format template (stray ``{``).

    Only fields whose name is a valid Python identifier count — JSON examples
    inside a prompt (``{"reply": ...}``) produce pseudo-fields like
    ``"reply": ...`` that are filtered out, so a prompt that merely *contains*
    braces isn't mistaken for a template. ``{{`` escapes are handled by the
    stdlib parser. Positional fields (``{}``/``{0}``) are ignored: EmptyOS
    prompts format with keywords.
    """
    try:
        fields = set()
        for _lit, field_name, _spec, _conv in string.Formatter().parse(text):
            if field_name and _IDENT_RE.match(field_name):
                fields.add(field_name)
        return frozenset(fields)
    except ValueError:
        return None


def declare_prompts(app_id: str, **defaults: str) -> "PromptSet":
    """Register an app's prompt constants; returns the resolving accessor.

    Called once at module level in the adopted ``prompts.py``. Kwarg names are
    the lowercase of the constants they mirror. Re-declaration with the same
    keys overwrites (module reimport safe).
    """
    for name, default in defaults.items():
        if not isinstance(default, str):
            raise TypeError(f"prompt {app_id}.{name} default must be str, got {type(default).__name__}")
        entry = PromptEntry(
            app_id=app_id,
            name=name,
            default=default,
            placeholders=extract_placeholders(default),
        )
        _REGISTRY[entry.key] = entry
    return PromptSet(app_id)


class PromptSet:
    """Attribute-access resolver returned by :func:`declare_prompts`.

    ``PROMPTS.classify_system`` resolves override-or-default at access time —
    cheap (a stat on a cached file), but multi-turn loops should still bind it
    to a local before iterating (prefix-cache rule 2).
    """

    __slots__ = ("_app_id",)

    def __init__(self, app_id: str):
        self._app_id = app_id

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        key = f"{self._app_id}.{name}"
        if key not in _REGISTRY:
            raise AttributeError(f"prompt {key!r} is not declared")
        return resolve(self._app_id, name)

    def __getitem__(self, name: str) -> str:
        return self.__getattr__(name)

    def names(self) -> list[str]:
        return sorted(e.name for e in _REGISTRY.values() if e.app_id == self._app_id)


def load_overrides(path: Path) -> dict:
    """Parse an overrides file → ``{key: {text, updated, note?}}``. Fail-open:
    a missing or corrupt file returns ``{}`` (corruption noted for sweep)."""
    global _CORRUPT
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        _CORRUPT = f"{path}: {e}"
        return {}
    overrides = raw.get("overrides") if isinstance(raw, dict) else None
    if not isinstance(overrides, dict):
        _CORRUPT = f"{path}: missing 'overrides' object"
        return {}
    _CORRUPT = None
    return {
        k: v
        for k, v in overrides.items()
        if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("text"), str)
    }


def _cached_overrides(path: Path) -> dict:
    """Configured-path reads go through an mtime_ns cache; explicit paths don't."""
    global _CACHE
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        _CACHE = None
        return {}
    if _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]
    data = load_overrides(path)
    _CACHE = (mtime, data)
    return data


def _override_applies(entry: PromptEntry, text: str) -> bool:
    """An override applies unless the default is a real template and the
    override's placeholder set differs — then the caller's ``.format()`` would
    KeyError, so we fall back to the default instead of breaking the call site
    (covers defaults that drift after an override was saved)."""
    if not entry.placeholders:  # None or empty: plain prompt, free-text ok
        return True
    return extract_placeholders(text) == entry.placeholders


def resolve(app_id: str, name: str, default: str | None = None, *, path: Path | None = None) -> str:
    """Override-or-default for one prompt. Never raises on override problems."""
    entry = _REGISTRY.get(f"{app_id}.{name}")
    if entry is None:
        if default is None:
            raise KeyError(f"prompt {app_id}.{name} is not declared and no default given")
        entry = PromptEntry(app_id, name, default, extract_placeholders(default))
    if path is not None:
        data = load_overrides(path)
    elif _DATA_DIR is not None:
        data = _cached_overrides(overrides_path())
    else:
        return entry.default
    rec = data.get(entry.key)
    if rec and _override_applies(entry, rec["text"]):
        return rec["text"]
    return entry.default


def entries(app_id: str | None = None) -> list[PromptEntry]:
    rows = [e for e in _REGISTRY.values() if app_id is None or e.app_id == app_id]
    return sorted(rows, key=lambda e: e.key)


def apps() -> set[str]:
    return {e.app_id for e in _REGISTRY.values()}


def _write_overrides(path: Path, overrides: dict) -> None:
    """Atomic write (tmp + os.replace) so a concurrent reader never sees a
    torn file — the vault read-modify-write lesson applied to data/."""
    global _CACHE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": OVERRIDES_SCHEMA_VERSION, "overrides": overrides},
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _CACHE = None


MAX_OVERRIDE_HISTORY = 10


def set_override(key: str, text: str, *, path: Path | None = None, note: str = "") -> dict:
    """Validated write of one override. Returns ``{"ok": True, ...}`` or
    ``{"ok": False, "error": ..., "expected": [...]}. Unregistered keys and
    placeholder mismatches are rejected — the UI/CLI is the *validated* write
    path (hand-editing the JSON bypasses this and degrades to fall-back).

    The overwritten value (if any) is pushed onto a bounded ``history`` list
    (newest first, capped at ``MAX_OVERRIDE_HISTORY``) so a bad edit can be
    rolled back via ``rollback_override`` — found missing during the 2026-08
    gap-analysis pass; every prior override was silently discarded."""
    path = Path(path) if path is not None else overrides_path()
    entry = _REGISTRY.get(key)
    if entry is None:
        return {"ok": False, "error": f"prompt {key!r} is not registered"}
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": "override text is empty"}
    if entry.placeholders:
        got = extract_placeholders(text)
        if got != entry.placeholders:
            return {
                "ok": False,
                "error": "placeholder mismatch — the override must keep the default's {placeholders}",
                "expected": sorted(entry.placeholders),
                "got": sorted(got) if got is not None else None,
            }
    overrides = load_overrides(path)
    prior = overrides.get(key)
    history = list(prior.get("history") or []) if prior else []
    if prior and prior.get("text") != text:
        history.insert(0, {"text": prior["text"], "updated": prior.get("updated", ""), "note": prior.get("note", "")})
        history = history[:MAX_OVERRIDE_HISTORY]
    overrides[key] = {"text": text, "updated": datetime.now().isoformat(timespec="seconds")}
    if note:
        overrides[key]["note"] = note
    if history:
        overrides[key]["history"] = history
    _write_overrides(path, overrides)
    return {"ok": True, "key": key}


def rollback_override(key: str, index: int, *, path: Path | None = None) -> dict:
    """Restore history entry ``index`` (0 = most recent prior value) as the
    current override, pushing the current value into history in its place.
    Returns ``{"ok": False, "error": ...}`` for an unregistered key, no
    override, or an out-of-range index."""
    path = Path(path) if path is not None else overrides_path()
    overrides = load_overrides(path)
    current = overrides.get(key)
    if not current:
        return {"ok": False, "error": f"no override exists for {key!r}"}
    history = list(current.get("history") or [])
    if not (0 <= index < len(history)):
        return {"ok": False, "error": f"history index {index} out of range (0..{len(history) - 1})"}
    restored = history.pop(index)
    history.insert(0, {"text": current["text"], "updated": current.get("updated", ""), "note": current.get("note", "")})
    overrides[key] = {
        "text": restored["text"],
        "updated": datetime.now().isoformat(timespec="seconds"),
        "history": history[:MAX_OVERRIDE_HISTORY],
    }
    if restored.get("note"):
        overrides[key]["note"] = restored["note"]
    _write_overrides(path, overrides)
    return {"ok": True, "key": key}


def clear_override(key: str, *, path: Path | None = None) -> bool:
    """Remove one override; True if it existed."""
    path = Path(path) if path is not None else overrides_path()
    overrides = load_overrides(path)
    if key not in overrides:
        return False
    del overrides[key]
    _write_overrides(path, overrides)
    return True


def override_status(path: Path | None = None) -> dict[str, dict]:
    """``{key: {active, stale, updated, note?}}`` for every stored override.
    ``stale`` = stored but not applying (placeholder drift vs the current
    default). Unregistered keys are omitted — see :func:`sweep` for those."""
    path = Path(path) if path is not None else overrides_path()
    out: dict[str, dict] = {}
    for key, rec in load_overrides(path).items():
        entry = _REGISTRY.get(key)
        if entry is None:
            continue
        applies = _override_applies(entry, rec["text"])
        out[key] = {
            "active": applies,
            "stale": not applies,
            "updated": rec.get("updated", ""),
            **({"note": rec["note"]} if rec.get("note") else {}),
        }
    return out


def sweep(path: Path | None = None) -> dict:
    """Drift report — the verb-registry "drift is loud" analogue.

    Returns ``{orphans, stale, corrupt, registered, overridden}`` where
    orphans = override keys no longer registered by any loaded prompts module
    (possibly an app not imported yet — the caller decides how loudly to
    report) and stale = placeholder-drifted overrides currently falling back.
    """
    path = Path(path) if path is not None else overrides_path()
    data = load_overrides(path)
    orphans = sorted(k for k in data if k not in _REGISTRY)
    stale = sorted(
        k for k, rec in data.items()
        if k in _REGISTRY and not _override_applies(_REGISTRY[k], rec["text"])
    )
    return {
        "orphans": orphans,
        "stale": stale,
        "corrupt": _CORRUPT,
        "registered": len(_REGISTRY),
        "overridden": len(data),
    }


def reset_for_tests() -> None:
    """Clear all module state. Test fixture use only."""
    global _DATA_DIR, _CACHE, _CORRUPT
    _REGISTRY.clear()
    _DATA_DIR = None
    _CACHE = None
    _CORRUPT = None
