"""Voice-assistant — generic fallback verbs (aura.open / aura.search / aura.app_list).

The long tail of ~200 apps has no hand-wired voice verb. These three generic
verbs make EVERY app reachable without per-app work: open any app by fuzzy name,
search the vault, or list any app's records (when it exposes the boards
``list_all`` contract). Server-side fuzzy resolution keeps the 200-app catalog
OUT of the prompt — the model passes a name, we resolve it here.

Gated by ``feature.companion-generic-verbs.enabled`` (a load-time filter in
app.py's intent build drops these three verbs from ``_intents`` when off, so the
manifest declarations are inert until the flag flips). Bound onto
``VoiceAssistantApp`` per ``.claude/rules/multi-module-apps.md``.

Reaches into other modules: ``self.kernel.apps`` (loader manifests + instances),
``self.search`` (the search capability). Do not import from ``.app`` (cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only

# The verb ids gated by feature.companion-generic-verbs.enabled (app.py reads this).
GENERIC_VERBS: tuple[str, ...] = ("aura.open", "aura.search", "aura.app_list")


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _resolve_app_query     = _generic_verbs._resolve_app_query
#   voice_open_app         = _generic_verbs.voice_open_app
#   voice_universal_search = _generic_verbs.voice_universal_search
#   voice_app_list         = _generic_verbs.voice_app_list
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


def _app_prefix(app_id: str, manifest) -> str:
    """The app's web prefix (from [provides.web].prefix), defaulting to /<id>/."""
    try:
        pfx = (manifest.provides.get("web") or {}).get("prefix")
    except Exception:
        pfx = None
    return (pfx or f"/{app_id}").rstrip("/") + "/"


def _resolve_app_query(self, query: str):
    """Fuzzy-resolve a spoken app name to ``(app_id, manifest)`` or ``None``.

    Match order: exact id / alias → exact name → name substring → word overlap
    in name+description. Only enabled apps are considered so we never route to a
    disabled/parked app. The 200-app catalog never enters the prompt — the model
    passes a name, this resolves it server-side.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    loader = getattr(self.kernel, "apps", None)
    if loader is None:
        return None
    try:
        enabled = loader.enabled_ids()
    except Exception:
        enabled = None
    mans = []
    for aid, m in loader.manifests.items():
        if enabled is not None and aid not in enabled:
            continue
        mans.append((aid, m))

    # 1. exact id or alias
    for aid, m in mans:
        if q == aid.lower() or q in [str(a).lower() for a in (getattr(m, "aliases", None) or [])]:
            return (aid, m)
    # 2. exact name
    for aid, m in mans:
        if q == (getattr(m, "name", "") or "").lower():
            return (aid, m)
    # 3. name substring (either direction)
    for aid, m in mans:
        nm = (getattr(m, "name", "") or "").lower()
        if nm and (q in nm or nm in q):
            return (aid, m)
    # 4. word overlap in name + description
    qwords = {w for w in q.split() if w}
    best, best_score = None, 0
    for aid, m in mans:
        hay = ((getattr(m, "name", "") or "") + " " + (getattr(m, "description", "") or "")).lower()
        score = sum(1 for w in qwords if w in hay)
        if score > best_score:
            best, best_score = (aid, m), score
    return best if best_score > 0 else None


async def voice_open_app(self, app: str = "") -> dict:
    """Generic verb — open any app by fuzzy name."""
    match = _resolve_app_query(self, app)
    if not match:
        return {"say": f"I couldn't find an app matching '{app}'."}
    aid, m = match
    name = getattr(m, "name", aid)
    return {"say": f"Opening {name}.",
            "link": {"text": f"Open {name}", "href": _app_prefix(aid, m)}}


async def voice_universal_search(self, query: str = "") -> dict:
    """Generic verb — search the vault and show the top hits."""
    q = (query or "").strip()
    if not q:
        return {"say": "What should I search for?"}
    try:
        results = await self.search(q)
    except Exception:
        results = []
    items = []
    for r in (results or [])[:6]:
        path = r if isinstance(r, str) else (r.get("path") if isinstance(r, dict) else "")
        if not path:
            continue
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        items.append({"text": name})
    if not items:
        return {"say": f"I didn't find anything for '{q}'."}
    return {
        "say": f"Found {len(items)} result(s) for {q}.",
        "card": {"renderer": "task-list", "title": f"Search · {q}", "data": items},
    }


async def voice_app_list(self, app: str = "") -> dict:
    """Generic verb — list an app's records when it exposes ``list_all``."""
    match = _resolve_app_query(self, app)
    if not match:
        return {"say": f"I couldn't find an app matching '{app}'."}
    aid, m = match
    name = getattr(m, "name", aid)
    prefix = _app_prefix(aid, m)
    inst = None
    try:
        inst = self.kernel.apps.instances.get(aid)
    except Exception:
        inst = None
    fn = getattr(inst, "list_all", None) if inst is not None else None
    if not callable(fn):
        return {"say": f"{name} doesn't have a quick list — opening it.",
                "link": {"text": f"Open {name}", "href": prefix}}
    try:
        rows = await fn()
    except Exception as e:
        return {"say": f"Couldn't list {name} — {e}."}
    rows = rows or []
    items = []
    for r in rows[:8]:
        if isinstance(r, dict):
            txt = r.get("title") or r.get("name") or r.get("text") or r.get("id") or ""
        else:
            txt = str(r)
        if txt:
            items.append({"text": str(txt)})
    say = f"{len(rows)} item(s) in {name}." + (" Here are the first few." if items else "")
    out = {"say": say, "link": {"text": f"Open {name}", "href": prefix}}
    if items:
        out["card"] = {"renderer": "task-list", "title": name, "data": items}
    return out
