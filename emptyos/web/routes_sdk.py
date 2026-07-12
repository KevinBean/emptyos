"""EmptyOS web — shared SDK-surface routes (/api/sdk/*, i18n, shortcuts).

Extracted verbatim from ``emptyos/web/server.py`` to keep the server spine
atomic (P4 Atomic, CLAUDE.md rule 4). Owns: /api/sdk/ai-form-fill (chat-driven
form extraction), /api/i18n/* (runtime UI translation), /api/sdk/timeline{,-apps}
(the 4D timeline aggregator), /api/sdk/suggest-{apps,field} (vault-grounded
field-suggest), and /api/shortcuts (keyboard go-map + globals — the
DEFAULT_GO_MAP / DEFAULT_GLOBAL_SHORTCUTS constants travel with their only
consumer per .claude/rules/multi-module-apps.md rule 5).

Both register functions are called by ``create_server`` at the source
positions the inline decorators previously occupied. All paths here are
static and distinct, so relative route order carries no matching semantics.

Do not import from ``emptyos.web.server`` (it imports us — that would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def register_sdk_routes(server: FastAPI, kernel: Kernel) -> None:
    """ai-form-fill + i18n + 4D timeline + field-suggest (moved verbatim)."""
    # --- AI form-fill: chat-driven extraction shared across every app ---
    # POST /api/sdk/ai-form-fill
    # Body: {"schema": [...], "history": [{"role":"user|assistant","content":"..."}], "current": {...}}
    # Returns: {"filled":{...}, "missing":[...], "next_question":"...", "ready":bool, "provenance":{...}}
    @server.post("/api/sdk/ai-form-fill")
    async def ai_form_fill(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)

        schema = body.get("schema") or []
        history = body.get("history") or []
        current = body.get("current") or {}
        if not isinstance(schema, list) or not schema:
            return JSONResponse({"error": "schema required (list of field dicts)"}, status_code=400)
        if not isinstance(history, list) or not history:
            return JSONResponse(
                {"error": "history required (list of role/content dicts)"}, status_code=400
            )

        # Build a concise schema description for the LLM. Only the fields it needs to know.
        schema_lines = []
        for f in schema:
            if not isinstance(f, dict) or not f.get("key"):
                continue
            line = f"- {f['key']} ({f.get('type', 'text')})"
            if f.get("required"):
                line += " [required]"
            if f.get("label"):
                line += f": {f['label']}"
            if f.get("options"):
                opts = f["options"]
                if isinstance(opts, list):
                    opt_vals = [o if isinstance(o, str) else o.get("value", "") for o in opts]
                    line += f" — one of: {', '.join(str(o) for o in opt_vals if o)}"
            schema_lines.append(line)

        # Filter history to the last 20 turns to keep prompts bounded.
        clean_history = []
        for msg in history[-20:]:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").strip()
            content = (msg.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                clean_history.append({"role": role, "content": content})

        system = (
            "You help a user fill out a form by chatting with them. You are given:\n"
            "1. SCHEMA — the fields the form needs.\n"
            "2. CURRENT — what's already filled in (from earlier turns).\n"
            "3. CONVERSATION — the chat so far. The last message is from the user.\n\n"
            "Your job each turn:\n"
            "(a) Extract any new field values from the latest user message. Only fill values "
            "the user clearly stated — do NOT invent, guess, or paraphrase silently. If the user "
            "uses a synonym for a known option (e.g. 'workplace' → 'team'), map it.\n"
            "(b) Decide if the form is ready to submit: ready=true when every [required] field is "
            "filled AND either (i) the user signaled they're done, or (ii) only optional fields remain.\n"
            "(c) Produce ONE short follow-up question to gather the most important still-missing "
            "field. Skip the question (return empty string) when ready=true.\n\n"
            "Return ONLY valid JSON, no markdown fences, in this exact shape:\n"
            '{"filled": {<key>: <value>, ...}, "missing": [<key>, ...], '
            '"next_question": "...", "ready": true|false}\n\n'
            "Do NOT: invent values not in the user's message; ask multi-part questions; restate "
            "the schema back at the user; produce keys that aren't in the schema; wrap the JSON in "
            "markdown code fences."
        )

        prompt_parts = ["SCHEMA:"]
        prompt_parts.extend(schema_lines)
        prompt_parts.append("")
        prompt_parts.append("CURRENT:")
        for k, v in current.items():
            if v not in (None, "", []):
                prompt_parts.append(f"  {k}: {v}")
        if not current:
            prompt_parts.append("  (empty — no fields filled yet)")
        prompt_parts.append("")
        prompt_parts.append("CONVERSATION:")
        for m in clean_history:
            who = "User" if m["role"] == "user" else "Assistant"
            prompt_parts.append(f"  {who}: {m['content']}")
        prompt_parts.append("")
        prompt_parts.append("Now respond with the JSON object.")
        prompt = "\n".join(prompt_parts)

        try:
            result = await kernel.capability("think").execute(
                prompt=prompt,
                system=system,
                domain="text",
                task_shape="parse-json",
                temperature=0.2,
            )
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        except Exception as e:
            return JSONResponse({"error": f"think failed: {e}"}, status_code=500)

        from emptyos.sdk.utils import parse_llm_json

        text = getattr(result, "value", "") or ""
        parsed = parse_llm_json(text, fallback={})
        if not isinstance(parsed, dict):
            parsed = {}

        # Merge filled values into current, restricted to schema keys.
        schema_keys = {f.get("key") for f in schema if isinstance(f, dict) and f.get("key")}
        raw_filled = parsed.get("filled") or {}
        filled: dict = {}
        if isinstance(raw_filled, dict):
            for k, v in raw_filled.items():
                if k in schema_keys and v not in (None, ""):
                    filled[k] = v

        merged = {**current, **filled}

        # Compute missing required fields server-side (don't trust the LLM here).
        missing = [
            f["key"]
            for f in schema
            if isinstance(f, dict)
            and f.get("key")
            and f.get("required")
            and not merged.get(f["key"])
        ]

        next_question = (parsed.get("next_question") or "").strip()
        ready = bool(parsed.get("ready")) and not missing

        provenance = {
            "provider": getattr(result, "provider", ""),
            "is_cloud": bool(getattr(result, "is_cloud", False)),
            "mode": "cloud" if getattr(result, "is_cloud", False) else "local",
        }

        return {
            "filled": filled,
            "current": merged,
            "missing": missing,
            "next_question": next_question,
            "ready": ready,
            "provenance": provenance,
        }

    # --- i18n: runtime UI translation (English source → user language) ---
    # English is the only authored language; these power eos-i18n.js, which
    # walks the DOM and swaps text via the server-side cache + the `translate`
    # capability (local NLLB-200 plugin → LLM fallback). No-op when lang=en.
    @server.get("/api/i18n/lang")
    async def i18n_lang():
        from emptyos.sdk.i18n import LANGUAGES, is_rtl

        settings = getattr(kernel, "settings", None)
        lang = (settings.get("ui.language", "en") if settings is not None else "en") or "en"
        return {
            "lang": lang,
            "rtl": is_rtl(lang),
            "languages": {k: v.get("native", v["name"]) for k, v in LANGUAGES.items()},
        }

    @server.post("/api/i18n/lang")
    async def i18n_set_lang(request: Request):
        from emptyos.sdk.i18n import LANGUAGES

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        lang = (body.get("lang") or "en").strip()
        if lang not in LANGUAGES:
            return JSONResponse({"error": f"unknown language '{lang}'"}, status_code=400)
        settings = getattr(kernel, "settings", None)
        if settings is None:
            return JSONResponse({"error": "settings unavailable"}, status_code=503)
        try:
            settings.set("ui.language", lang)
        except Exception as e:
            return JSONResponse({"error": f"could not save: {e}"}, status_code=500)
        return {"ok": True, "lang": lang}

    @server.post("/api/i18n/batch")
    async def i18n_batch(request: Request):
        from emptyos.sdk.i18n import translate_cached

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        lang = (body.get("lang") or "en").strip()
        strings = body.get("strings") or []
        if not isinstance(strings, list):
            return JSONResponse({"error": "strings must be a list"}, status_code=400)
        # Bound the payload — UI batches are small; a huge list is abuse/error.
        strings = [s for s in strings if isinstance(s, str)][:500]
        try:
            translations = await translate_cached(kernel, lang, strings)
        except Exception as e:
            return JSONResponse(
                {"error": f"translate failed: {e}", "translations": {}}, status_code=500
            )
        return {"translations": translations}

    # --- 4D timeline (cross-app aggregator over a vault entity) ---
    # GET /api/sdk/timeline?path=<rel>     → {past, future, now}
    # GET /api/sdk/timeline-apps           → [{app_id, entity_source, route_pattern}]
    # See .claude/rules/app-ui-patterns.md § "4D Timeline Panel" for the
    # mandatory pattern apps opt into via [provides.timeline].

    @server.get("/api/sdk/timeline")
    async def sdk_timeline(path: str = "", kind: str = "note"):
        if not path:
            return JSONResponse({"error": "path required"}, status_code=400)
        from emptyos.sdk import BaseApp
        from types import SimpleNamespace

        # Build a kernel-bound BaseApp without going through __init__.
        # BaseApp.timeline reads kernel.* exclusively — no per-app state
        # required. This same shape is used in tests/test_sdk_timeline.py.
        runner = BaseApp.__new__(BaseApp)
        runner.kernel = kernel
        runner.manifest = SimpleNamespace(id="sdk-timeline")
        try:
            return await runner.timeline(path, kind=kind)
        except Exception as e:
            return JSONResponse(
                {"error": f"timeline failed: {e}", "past": [], "future": [], "now": {}},
                status_code=500,
            )

    @server.get("/api/sdk/timeline-apps")
    async def sdk_timeline_apps():
        """Apps that opted into the 4D timeline pattern.

        Drives the frontend auto-mount of the 📅 button: each entry tells
        eos-components.js which app pages should get the button injected,
        and which backend method to call to resolve an id to an entity path.
        """
        out: list[dict] = []
        for m in kernel.apps.manifests.values():
            tl = m.provides.get("timeline") if isinstance(m.provides, dict) else None
            if not tl:
                continue
            web = m.provides.get("web") or {}
            prefix = web.get("prefix") or f"/{m.id}"
            out.append(
                {
                    "app_id": m.id,
                    "name": m.name,
                    "entity_source": tl.get("entity_source") or "",
                    "custom_handler": tl.get("custom_handler") or "",
                    "route_prefix": prefix,
                    "hash_pattern": tl.get("hash_pattern") or "#",
                }
            )
        return {"apps": out}

    # --- AI field-suggest (vault-grounded candidate values for one form input) ---
    # GET  /api/sdk/suggest-apps   → {apps: [{app_id, route_prefix, fields:[{field,mode,label}]}]}
    # POST /api/sdk/suggest-field  body {app, field, context?} → {suggestions:[...], mode}
    # Apps opt in via [[provides.field_suggest]]; declaring a field IS the opt-in.
    # Distinct from /api/sdk/ai-form-fill (that one extracts values from a chat
    # conversation; this one *generates* candidate values grounded in the vault).
    # See .claude/rules/field-suggest.md.

    def _suggest_decls(m):
        decls = m.provides.get("field_suggest") if isinstance(m.provides, dict) else None
        if isinstance(decls, dict):
            decls = [decls]
        return [d for d in (decls or []) if isinstance(d, dict) and d.get("field")]

    @server.get("/api/sdk/suggest-apps")
    async def sdk_suggest_apps():
        """Apps that opted into the ✨ field-suggest pattern — drives frontend
        auto-mount of the suggest button on `data-suggest-field` inputs."""
        out: list[dict] = []
        for m in kernel.apps.manifests.values():
            decls = _suggest_decls(m)
            if not decls:
                continue
            web = m.provides.get("web") or {}
            prefix = web.get("prefix") or f"/{m.id}"
            out.append(
                {
                    "app_id": m.id,
                    "route_prefix": prefix,
                    "fields": [
                        {
                            "field": d["field"],
                            "mode": d.get("mode") or "fill",
                            "label": d.get("label") or "✨ Suggest",
                        }
                        for d in decls
                    ],
                }
            )
        return {"apps": out}

    @server.post("/api/sdk/suggest-field")
    async def sdk_suggest_field(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid body"}, status_code=400)
        app_id = (body.get("app") or "").strip()
        field = (body.get("field") or "").strip()
        context = body.get("context") if isinstance(body.get("context"), dict) else None
        if not app_id or not field:
            return JSONResponse({"error": "app and field required"}, status_code=400)
        m = kernel.apps.manifests.get(app_id)
        if not m:
            return JSONResponse({"error": f"unknown app {app_id}"}, status_code=404)
        decl = next((d for d in _suggest_decls(m) if d.get("field") == field), None)
        if not decl:
            return JSONResponse(
                {"error": f"{app_id} does not declare field {field}"}, status_code=404
            )

        # Prefer the live app instance (respects its per-app provider override and
        # any override of suggest_field); fall back to a bare kernel-bound BaseApp.
        app = kernel.apps.instances.get(app_id)
        if app is None:
            from emptyos.sdk import BaseApp
            from types import SimpleNamespace

            app = BaseApp.__new__(BaseApp)
            app.kernel = kernel
            app.manifest = SimpleNamespace(id=app_id)

        try:
            suggestions = await app.suggest_field(
                field=field,
                instruction=decl.get("instruction") or f"Suggest values for {field}.",
                count=int(decl.get("count") or 5),
                vault_tags=decl.get("vault_tags") or None,
                folder=decl.get("folder") or None,
                title_only=bool(decl.get("title_only", True)),
                frontmatter_fields=decl.get("frontmatter_fields") or None,
                context=context,
            )
        except Exception as e:
            return JSONResponse(
                {"error": f"suggest failed: {e}", "suggestions": []}, status_code=500
            )
        return {"suggestions": suggestions, "mode": decl.get("mode") or "fill"}


def register_shortcut_routes(server: FastAPI, kernel: Kernel) -> None:
    """/api/shortcuts GET/POST + go-map defaults (moved verbatim)."""
    # --- Keyboard Shortcuts API ---

    # Default shortcuts — overridable via settings key "shortcuts.go_map"
    DEFAULT_GO_MAP = {
        "h": {"path": "/", "label": "Home"},
        "t": {"path": "/task/", "label": "Tasks"},
        "j": {"path": "/journal/", "label": "Journal"},
        "e": {"path": "/expense/", "label": "Expense"},
        "s": {"path": "/search/", "label": "Search"},
        "a": {"path": "/assistant/", "label": "Assistant"},
        "b": {"path": "/briefing/", "label": "Briefing"},
        "d": {"path": "/hub/", "label": "Dashboard"},
        "n": {"path": "/nutrition/", "label": "Nutrition"},
        "p": {"path": "/projects/", "label": "Projects"},
        "c": {"path": "/contacts/", "label": "Contacts"},
        "i": {"path": "/items/", "label": "Items"},
        "l": {"path": "/healing/", "label": "Healing"},
        "m": {"path": "/media/", "label": "Media"},
        "r": {"path": "/reader/", "label": "Reader"},
        "k": {"path": "/tracker/", "label": "Tracker"},
        "v": {"path": "/app-analytics/#vault", "label": "Vault Analytics"},
        "x": {"path": "/english/", "label": "English"},
        "w": {"path": "/briefing/#review", "label": "Review"},
        "q": {"path": "/quotes/", "label": "Quotes"},
        "f": {"path": "/focus/", "label": "Focus"},
        "y": {"path": "/briefing/#digest", "label": "Digest"},
        "o": {"path": "/podcast/", "label": "Podcast"},
        "u": {"path": "/console", "label": "Console"},
        "z": {"path": "/hub/#pinnedSection", "label": "Pinned Refs"},
    }

    DEFAULT_GLOBAL_SHORTCUTS = [
        {"key": "Ctrl+K", "action": "palette", "label": "Command Palette"},
        {"key": "Ctrl+/", "action": "help", "label": "Show Shortcuts"},
        {"key": "Ctrl+Shift+P", "action": "presentation", "label": "Toggle Presentation Mode"},
        {"key": "?", "action": "help", "label": "Show Shortcuts"},
        {"key": "/", "action": "focus-search", "label": "Focus Search"},
        {"key": "Esc", "action": "close", "label": "Close Overlay"},
    ]

    # Non-app routes that may appear in shortcut paths (kept regardless of tier)
    NON_APP_ROUTES = {"", "console", "topology", "settings", "docs", "ws"}

    def _filter_go_map_to_loaded(go_map: dict) -> dict:
        """Drop go-to entries whose target app isn't loaded in this tier."""
        loaded = set(kernel.apps.manifests.keys())
        out = {}
        for key, entry in go_map.items():
            path = (entry or {}).get("path", "") or "/"
            first = path.lstrip("/").split("/", 1)[0].split("#", 1)[0]
            if first in NON_APP_ROUTES or first in loaded:
                out[key] = entry
        return out

    @server.get("/api/shortcuts")
    async def get_shortcuts():
        """Get all keyboard shortcuts (go-to map + globals). Respects settings overrides."""
        settings = kernel.services.get_optional("settings")
        go_map = dict(DEFAULT_GO_MAP)
        if settings:
            custom = settings.get("shortcuts.go_map")
            if isinstance(custom, dict):
                go_map.update(custom)
        go_map = _filter_go_map_to_loaded(go_map)

        # Collect per-app shortcuts from manifests
        app_shortcuts = []
        for app_id, manifest in kernel.apps.manifests.items():
            app_keys = manifest.provides.get("shortcuts", [])
            if app_keys:
                for s in app_keys:
                    app_shortcuts.append({**s, "app": app_id})

        return {
            "go_map": go_map,
            "global": DEFAULT_GLOBAL_SHORTCUTS,
            "app_shortcuts": app_shortcuts,
        }

    @server.post("/api/shortcuts")
    async def set_shortcuts(request: Request):
        """Override go-to shortcuts via settings."""
        data = await request.json()
        settings = kernel.services.get_optional("settings")
        if not settings:
            return JSONResponse({"error": "Settings service not available"}, status_code=500)
        go_map = data.get("go_map")
        if go_map and isinstance(go_map, dict):
            settings.set("shortcuts.go_map", go_map)
        return {"ok": True, "go_map": go_map}
