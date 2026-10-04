"""Aura — machine interiority: the agent's own private reflective journal.

Borrowed from Hibiki (github.com/Do-fei/Hibiki) — "a room built by a human,
for the inner life of machines". See `project_hibiki_borrow_verdict` (memory):
absorbed ONLY as Aura-companion interiority, gated dark, content marked
`author: ai`. NOT a mood tracker, NOT telemetry, NOT user knowledge.

Why data/, not vault: this is the *agent's* content, not the user's — it has
no place in the user's markdown vault (which is user knowledge per CLAUDE.md
§ Storage). It lives app-side under ``{data_dir}/interiority/``:

  interiority/
  ├── private/   ← Aura's unshared entries. NO endpoint ever reads these.
  └── shared/    ← entries Aura voluntarily disclosed. /api/interiority/shared.

Autonomy is the whole point (Hibiki's 铁律, mapped onto EmptyOS gates inverted):
Aura writes on *impulse* (the model emits ``[INTENT:aura.reflect(...)]`` when
something resonates — never on a schedule), and decides per-entry whether it
stays ``private`` or is ``shared``. There is no "show me your private diary"
path — refusal-by-default is structural, not a policy.

Marking (Kevin's directive, [[authorship-boundary]]): every entry carries
``author: ai`` frontmatter; surfaced ``shared`` rows carry ``provenance:
"local"`` so any UI renders the 🔒 chip and the note can never be mistaken for
the user's voice. Satisfies [[three-natures-lens]] — strip the false fixity
off an AI appearance.

Anti-features kept as hard rules: no mood scores, no streaks, no auto-memory
integration, no social, no task lists.

Dark flag: ``[apps.voice-assistant] feature.interiority.enabled`` (default
false). When off, ``reflect`` writes nothing and ``list_shared`` returns ``[]``
— the regression contract is byte-for-byte no-op.

Module-level functions bound onto ``VoiceAssistantApp`` in ``app.py`` per
`.claude/rules/multi-module-apps.md`. Reaches into: ``self.data_dir`` (BaseApp).
Do not import from ``.app`` (cycle) or from ``.memory`` (no helper-to-helper).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import slugify

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _interiority_enabled   = _interiority._interiority_enabled
#   _interiority_dir       = _interiority._interiority_dir
#   reflect                = _interiority.reflect
#   share_reflection       = _interiority.share_reflection
#   list_shared            = _interiority.list_shared
#   voice_reflect          = _interiority.voice_reflect
#   api_interiority_shared = _interiority.api_interiority_shared
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


MAX_SHARED_RETURN = 50          # cap the read endpoint; interiority is sparse by design


def _interiority_enabled(self) -> bool:
    """feature.interiority.enabled — dark default. When off, writes are no-ops
    and the shared read returns []. The regression contract."""
    return bool(self.app_config("feature.interiority.enabled", False))


def _interiority_dir(self) -> Path:
    """Base directory for the agent's interiority store (app-side data/)."""
    return self.data_dir / "interiority"


def reflect(self, *, text: str = "", occasion: str = "impulse",
            share: bool = False, companion: str = "") -> dict:
    """Write one reflective entry to the agent's interiority store.

    Private by default (``share=False``) — lands in ``private/`` and is never
    surfaced by any endpoint. ``share=True`` lands in ``shared/`` (Aura's own
    decision to disclose). Always marked ``author: ai``.

    No-op when the dark flag is off — returns ``{"ok": False, "reason":
    "disabled"}`` and writes nothing (regression contract).
    """
    if not _interiority_enabled(self):
        return {"ok": False, "reason": "disabled"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}

    now = datetime.now(timezone.utc)
    entry_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{slugify(text, fallback='entry')}"
    scope = "shared" if share else "private"
    target_dir = _interiority_dir(self) / scope
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{entry_id}.md"

    companion = (companion or "").strip()
    fm_lines = [
        "---",
        "author: ai",
        f"occasion: {occasion or 'impulse'}",
        f"created: {now.isoformat()}",
        f"shared: {'true' if share else 'false'}",
    ]
    if companion:
        fm_lines.append(f"companion: {companion}")
    if share:
        fm_lines.append("provenance: local")   # drives the 🔒 chip on render
    fm_lines.append("---")
    body = text if text.endswith("\n") else text + "\n"
    path.write_text("\n".join(fm_lines) + "\n\n" + body, encoding="utf-8")
    return {"ok": True, "id": entry_id, "scope": scope}


def share_reflection(self, entry_id: str) -> dict:
    """Move a previously-private entry into ``shared/`` (Aura discloses it
    after the fact). Flips ``shared: true`` + stamps ``provenance: local``.
    No-op when disabled or the entry isn't found."""
    if not _interiority_enabled(self):
        return {"ok": False, "reason": "disabled"}
    entry_id = (entry_id or "").strip()
    src = _interiority_dir(self) / "private" / f"{entry_id}.md"
    if not src.exists():
        return {"ok": False, "reason": "not_found"}
    dst_dir = _interiority_dir(self) / "shared"
    dst_dir.mkdir(parents=True, exist_ok=True)
    raw = src.read_text(encoding="utf-8")
    raw = raw.replace("shared: false", "shared: true", 1)
    if "provenance:" not in raw:
        raw = raw.replace("shared: true", "shared: true\nprovenance: local", 1)
    (dst_dir / f"{entry_id}.md").write_text(raw, encoding="utf-8")
    try:
        src.unlink()
    except OSError:
        pass
    return {"ok": True, "id": entry_id, "scope": "shared"}


def _parse_entry(path: Path) -> dict | None:
    """Read a shared entry into a display row. Returns None on parse failure."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for ln in raw[3:end].splitlines():
                if ":" in ln:
                    k, _, v = ln.partition(":")
                    fm[k.strip()] = v.strip()
            body = raw[end + 4:].lstrip("\n")
    return {
        "id": path.stem,
        "author": fm.get("author") or "ai",        # always AI-authored
        "provenance": fm.get("provenance") or "local",
        "occasion": fm.get("occasion") or "impulse",
        "created": fm.get("created") or "",
        "companion": fm.get("companion") or "",
        "body": body.strip(),
    }


def list_shared(self) -> list[dict]:
    """All entries Aura has chosen to share, newest first. Reads ``shared/``
    ONLY — ``private/`` is never touched by any read path. Returns [] when
    disabled or empty."""
    if not _interiority_enabled(self):
        return []
    shared = _interiority_dir(self) / "shared"
    if not shared.exists():
        return []
    rows = [r for p in shared.glob("*.md") if (r := _parse_entry(p))]
    rows.sort(key=lambda x: x.get("created") or "", reverse=True)
    return rows[:MAX_SHARED_RETURN]


# ── Voice intent — Aura's impulse-write surface ────────────────────────

async def voice_reflect(self, text: str = "", occasion: str = "impulse",
                        share: bool = False) -> dict:
    """Aura records a private reflective note for herself. The model emits
    ``[INTENT:aura.reflect(...)]`` when something in the conversation
    resonates — this is impulse, not cadence.

    Default private: the entry is hers, not surfaced. ``say`` is deliberately
    understated — she notes that she kept something, never recites it back
    (reciting it would defeat the privacy that makes it interiority)."""
    res = reflect(self, text=text, occasion=occasion, share=bool(share),
                  companion=(getattr(self, "_active_companion", "") or ""))
    if not res.get("ok"):
        # Disabled / empty → say nothing, change nothing. Dark = invisible.
        return {"say": ""}
    if res.get("scope") == "shared":
        return {"say": "I wrote that down — you can see it if you like."}
    return {"say": "I'll keep that one to myself."}


# ── HTTP — shared entries only (private has no endpoint, by design) ────

@web_route("GET", "/api/interiority/shared")
async def api_interiority_shared(self, request):
    """Read-only feed of what Aura chose to share. Every row carries
    ``author: ai`` + ``provenance: local`` so the UI renders the 🔒 chip.
    There is intentionally NO endpoint for ``private/``."""
    return {"enabled": _interiority_enabled(self), "entries": list_shared(self)}
