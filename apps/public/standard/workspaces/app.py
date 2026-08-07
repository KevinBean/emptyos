"""Workspaces — curated, navigable groupings of apps around a goal.

A *workspace* (a "Space") bundles related apps under one landing surface, the
way "English Academy" gathers stories / courses / vocabulary / speaking. This
app is the generic shell: it aggregates membership from three sources and
renders a navigable landing per space. Domain-rich behaviour stays in each
space's optional ``hero_app`` (e.g. ``academy.dashboard()``); this app only
owns the grouping + the shell.

Membership sources, merged in increasing precedence:
  1. Built-in ``DEFAULT_SPACES`` — ships English Academy out of the box.
  2. Manifest opt-in — an app declares ``[provides.workspace] slug = "..."``.
  3. Config — ``[apps.workspaces]`` ``spaces`` (definitions) + ``members``
     (per-machine member-list overrides).
  4. Runtime overrides — ``data/apps/workspaces/overrides.json``, written by
     the in-app membership editor. This is the **editable single source of
     truth** for "which apps serve this archetype": compose each Space's app
     set here once; the runtime grouping reflects it and a shippable release
     bundle is *derived* from it on demand (``derive_bundle`` / the Space's
     ``…/bundle`` endpoint) rather than hand-maintaining a parallel tier list.

v1 is navigation + landing only. Each space definition may carry an inert
``context`` block (companion / kb_tags / think_domain) that a future v2 will
read to narrow Aura's domain, scope the KB, and pre-load a persona. It is
stored and returned but does nothing yet — shipping it now keeps v2 additive.
"""

from __future__ import annotations

import asyncio
import json
import re

from emptyos.sdk import BaseApp, cli_command, web_route

# ── Space-scoped Ask (Phase 3 — activates the `context` block) ────────
# A Space with a `context.kb_tags` (and optional persona/think_domain) gains a
# grounded "Ask" surface: retrieval is restricted to the Space's tags, the think
# call routes to the Space's domain, and the persona flavours the answer. This
# is the first concrete consumer of `context`; Aura/assistant scoping stays
# deferred (no callable scope API exists there yet — see the audit).
ASK_SYSTEM = (
    'You are the in-Space guide for the "{space}" workspace in EmptyOS. '
    "Answer the user's question using ONLY the provided context notes from "
    "their own vault.\n"
    "Rules:\n"
    "- Ground every claim in the context; name the note title when you use it.\n"
    "- If the context does not contain the answer, say so plainly and suggest "
    "what note to add — never invent facts, numbers, citations, or note titles.\n"
    "- Be concise and practical. No preamble, no 'as an AI', no filler.\n"
    "{persona}"
)
# Retrieval bounds — keep the (possibly cloud) prompt small + scoped.
_ASK_SCAN = 40      # how many tagged notes to keyword-rank
_ASK_TOP = 4        # how many to actually ground on
_ASK_CHARS = 1500   # per-note excerpt cap

# Built-in spaces. A default space is hidden from the index when none of its
# members are installed (keeps a fresh public clone clean), but stays reachable
# by direct slug. English Academy's rich hero comes from the `academy` app.
DEFAULT_SPACES = [
    {
        "slug": "english-academy",
        "title": "English Academy",
        "icon": "🗣️",
        "tagline": "Everything for learning English, in one place.",
        "order": 10,
        "hero_app": "academy",
        # The hero_app (academy) is the rich landing and is excluded from the
        # member grid automatically. Members carry curated title/blurb so the
        # Space (and academy's own page, which sources membership from here) get
        # human labels rather than raw app names.
        "members": [
            {"app": "learn", "title": "Courses", "icon": "📚", "blurb": "Follow structured lessons and review course cards."},
            {
                "app": "reader",
                "title": "Interactive stories",
                "icon": "\U0001F4D6",
                "blurb": "Choose what happens next in CEFR-graded stories, then turn them into short games.",
            },
            {"app": "dictionary", "title": "Vocabulary", "icon": "🔤", "blurb": "Look up words and review your vocabulary deck."},
            {"app": "speaking", "title": "Conversation", "icon": "🗣️", "blurb": "Practice speaking with guided feedback."},
            {"app": "lessons", "title": "Topical lessons", "icon": "📝", "blurb": "Work through focused English lesson material."},
            {"app": "shadowing", "title": "Shadowing", "icon": "🔁", "blurb": "Build rhythm and pronunciation by repeating audio."},
            {"app": "voice-review", "title": "Voice review", "icon": "🎧", "blurb": "Review recorded speaking practice and feedback."},
            {"app": "improv", "title": "Improv", "icon": "🎭", "blurb": "Practice flexible dialogue with an AI scene partner."},
            {"app": "radio", "title": "Listening radio", "icon": "📻", "blurb": "Train your ear with an English listening session."},
        ],
        # v1-inert; a future v2 reads this to scope Aura / KB / think.
        "context": {
            "companion": "emma",
            "kb_tags": ["english"],
            "think_domain": "text",
        },
    },
    {
        "slug": "engineering",
        "title": "Engineering",
        "icon": "⚡",
        "tagline": "Power-systems modelling, cabling, and 3D design tools.",
        "order": 20,
        # No hero_app — proves the generic shell works as a plain member grid.
        "hero_app": "",
        "members": [
            {"app": "power-study", "title": "Power study", "icon": "🔌", "blurb": "Build a power-flow and fault model of a network."},
            {"app": "cable-network", "title": "Cable network", "icon": "🧵", "blurb": "Design and rate a cable reticulation network."},
            {"app": "short-circuit", "title": "Short circuit", "icon": "⚡", "blurb": "IEC 60909 fault-level calculations."},
            {"app": "reliability", "title": "Reliability", "icon": "📊", "blurb": "IEEE 493 reliability and availability analysis."},
            {"app": "earthing", "title": "Earthing", "icon": "🌐", "blurb": "Earthing grid design and step / touch safety."},
            {"app": "lightning", "title": "Lightning", "icon": "🌩️", "blurb": "Lightning protection and risk assessment."},
            {"app": "cad", "title": "CAD", "icon": "📐", "blurb": "Parametric 3D modelling and engineering scenes."},
            {"app": "design-package", "title": "Design package", "icon": "📦", "blurb": "Assemble multi-discipline outputs into one PDF."},
            {"app": "sim", "title": "EMTP simulator", "icon": "〰️", "blurb": "Time-domain electromagnetic transient simulation."},
        ],
        "context": {},
    },
    {
        "slug": "news",
        "title": "News",
        "icon": "🗞️",
        "tagline": "Your brief, your feeds, and your reading list — in one live place.",
        "order": 30,
        "hero_app": "",
        "members": [
            {"app": "daily-brief", "title": "Daily Brief", "icon": "🗞️", "blurb": "Curated AI brief, feed inbox, and digest."},
            {"app": "bookmarks", "title": "Read later", "icon": "🔖", "blurb": "Saved articles to read when you have time."},
        ],
        # Live, dynamic surface — the brief + feeds + read-later running inline,
        # not just links. Replaces the retired standalone news-center reader.
        "widgets": [
            {"type": "panel", "app": "daily-brief", "method": "panel_today",
             "renderer": "stat-tile", "title": "Today"},
            {"type": "embed", "app": "daily-brief", "title": "Brief & feeds", "height": 640},
            {"type": "embed", "app": "bookmarks", "title": "Read later", "height": 420},
        ],
        "context": {},
    },
]


class WorkspacesApp(BaseApp):
    # ── Registry ────────────────────────────────────────────────────────

    def _registry(self, apply_overrides: bool = True) -> dict[str, dict]:
        """Merge the membership sources into ``{slug: space}``.

        ``apply_overrides=False`` returns the base (default + manifest + config)
        without the runtime-override layer — used by the editor write-path to
        find a Space's curated member entries before re-applying an edit."""
        spaces: dict[str, dict] = {}

        # 1. Built-in defaults.
        for s in DEFAULT_SPACES:
            spaces[s["slug"]] = {
                **s,
                "members": list(s.get("members", [])),
                "widgets": list(s.get("widgets", [])),
                "context": dict(s.get("context", {})),
                "_source": "default",
            }

        # 2. Manifest opt-ins — [provides.workspace] on any installed app.
        for m in self.kernel.apps.manifests.values():
            w = (getattr(m, "provides", None) or {}).get("workspace")
            if not isinstance(w, dict):
                continue
            slug = str(w.get("slug") or "").strip()
            if not slug:
                continue
            sp = spaces.get(slug)
            if sp is None:
                sp = {
                    "slug": slug,
                    "title": w.get("title") or slug.replace("-", " ").title(),
                    "icon": w.get("icon") or "",
                    "tagline": w.get("tagline") or "",
                    "order": _as_int(w.get("order"), 100),
                    "hero_app": w.get("hero_app") or "",
                    "members": [],
                    "widgets": [],
                    "context": {},
                    "_source": "manifest",
                }
                spaces[slug] = sp
            if m.id not in sp["members"]:
                sp["members"].append(m.id)

        # 3a. Config space definitions — add or override scalar fields.
        for e in (self.app_config("spaces", []) or []):
            if not isinstance(e, dict):
                continue
            slug = str(e.get("slug") or "").strip()
            if not slug:
                continue
            sp = spaces.get(slug) or {
                "slug": slug,
                "members": [],
                "context": {},
                "_source": "config",
            }
            for k in ("title", "icon", "tagline", "hero_app"):
                if e.get(k):
                    sp[k] = e[k]
            if "order" in e:
                sp["order"] = _as_int(e.get("order"), sp.get("order", 100))
            if isinstance(e.get("members"), list):
                sp["members"] = [str(x) for x in e["members"]]
            if isinstance(e.get("widgets"), list):
                sp["widgets"] = e["widgets"]
            if isinstance(e.get("context"), dict):
                sp["context"] = e["context"]
            sp.setdefault("title", slug.replace("-", " ").title())
            sp.setdefault("icon", "")
            sp.setdefault("tagline", "")
            sp.setdefault("order", 100)
            sp.setdefault("hero_app", "")
            sp.setdefault("members", [])
            sp.setdefault("widgets", [])
            sp.setdefault("context", {})
            spaces[slug] = sp

        # 3b. Config member overrides — replace a space's member list wholesale.
        members_cfg = self.app_config("members", {}) or {}
        if isinstance(members_cfg, dict):
            for slug, ids in members_cfg.items():
                if slug in spaces and isinstance(ids, list):
                    spaces[slug]["members"] = [str(x) for x in ids]

        # 4. Runtime overrides — the editable layer (highest precedence).
        if apply_overrides:
            for slug, o in (self._load_overrides().get("spaces", {}) or {}).items():
                if not isinstance(o, dict):
                    continue
                sp = spaces.get(slug)
                if sp is None:
                    sp = {"slug": slug, "members": [], "widgets": [],
                          "context": {}, "_source": "override"}
                    spaces[slug] = sp
                for k in ("title", "icon", "tagline"):
                    if o.get(k):
                        sp[k] = o[k]
                if isinstance(o.get("members"), list):
                    sp["members"] = list(o["members"])
                sp["_edited"] = True
                if o.get("created"):
                    sp["_user_created"] = True
                sp.setdefault("title", slug.replace("-", " ").title())
                sp.setdefault("icon", "")
                sp.setdefault("tagline", "")
                sp.setdefault("order", 100)
                sp.setdefault("hero_app", "")
                sp.setdefault("widgets", [])
                sp.setdefault("context", {})

        return spaces

    # ── Membership editor (runtime overrides — the editable source of truth) ──
    # Writes data/apps/workspaces/overrides.json so the per-archetype app set is
    # composed from one place. App-managed operational state (per the two-domain
    # rule), not human-authored prose → data/, not the vault.

    _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

    def _overrides_file(self):
        return self.data_dir / "overrides.json"

    def _load_overrides(self) -> dict:
        f = self._overrides_file()
        if not f.exists():
            return {"spaces": {}}
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return {"spaces": {}}
            d.setdefault("spaces", {})
            return d
        except Exception:
            return {"spaces": {}}

    def _save_overrides(self, data: dict) -> None:
        f = self._overrides_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def create_space(self, slug: str, title: str = "", icon: str = "", tagline: str = "") -> dict:
        slug = (slug or "").strip().lower()
        if not self._SLUG_RE.match(slug):
            return {"error": "slug must be lowercase letters/digits/hyphens (e.g. 'fde-engineering')"}
        if slug in self._registry(apply_overrides=False):
            return {"error": f"'{slug}' already exists as a built-in/config Space — edit it instead"}
        ov = self._load_overrides()
        if slug in ov["spaces"]:
            return {"error": f"Space '{slug}' already exists"}
        ov["spaces"][slug] = {
            "created": True,
            "title": (title or "").strip() or slug.replace("-", " ").title(),
            "icon": (icon or "").strip(),
            "tagline": (tagline or "").strip(),
            "members": [],
        }
        self._save_overrides(ov)
        return {"ok": True, "slug": slug}

    def set_members(self, slug: str, ids: list) -> dict:
        slug = (slug or "").strip()
        base = self._registry(apply_overrides=False).get(slug)
        ov = self._load_overrides()
        existing = ov["spaces"].get(slug)
        if base is None and existing is None:
            return {"error": "space not found"}
        # Preserve curated rich member entries (title/blurb/icon) where the id is
        # already known; new ids become plain {app: id} and resolve via manifest.
        rich: dict = {}
        for src in ((base or {}).get("members", []), (existing or {}).get("members", [])):
            for e in src:
                rich[self._member_app_id(e)] = e if isinstance(e, dict) else {"app": e}
        clean, seen = [], set()
        for i in ids:
            i = str(i).strip()
            if i and i not in seen:
                seen.add(i)
                clean.append(i)
        sp = ov["spaces"].setdefault(slug, {})
        sp["members"] = [rich.get(i, {"app": i}) for i in clean]
        self._save_overrides(ov)
        return {"ok": True, "slug": slug, "members": clean}

    def _current_member_ids(self, slug: str) -> list:
        sp = self._registry().get(slug) or {}
        out, seen = [], set()
        for e in sp.get("members", []):
            i = self._member_app_id(e)
            if i and i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def add_member(self, slug: str, app_id: str) -> dict:
        app_id = (app_id or "").strip()
        if not app_id:
            return {"error": "app id required"}
        ids = self._current_member_ids(slug)
        if app_id in ids:
            return {"ok": True, "slug": slug, "members": ids}
        ids.append(app_id)
        return self.set_members(slug, ids)

    def remove_member(self, slug: str, app_id: str) -> dict:
        target = (app_id or "").strip()
        return self.set_members(slug, [i for i in self._current_member_ids(slug) if i != target])

    def delete_space(self, slug: str) -> dict:
        slug = (slug or "").strip()
        ov = self._load_overrides()
        rec = ov["spaces"].get(slug)
        if not rec:
            return {"error": "no override for this Space (built-in Spaces can be emptied but not deleted)"}
        created = bool(rec.get("created"))
        del ov["spaces"][slug]
        self._save_overrides(ov)
        # created → fully removed; otherwise the override is dropped → reverts to base.
        return {"ok": True, "slug": slug, "deleted": created, "reverted": not created}

    def installable_apps(self) -> list:
        enabled = set(self.kernel.apps.enabled_ids())
        out = [
            {"id": m.id, "name": (getattr(m, "name", "") or m.id), "enabled": m.id in enabled}
            for m in self.kernel.apps.manifests.values()
        ]
        out.sort(key=lambda a: a["name"].lower())
        return out

    def derive_bundle(self, slug: str) -> dict | None:
        """Generate a shippable release-tier bundle from a Space's membership —
        the per-archetype app set, derived on demand (not a hand-kept tier)."""
        sp = self._registry().get(slug)
        if sp is None:
            return None
        ids, seen = [], set()
        hero = (sp.get("hero_app") or "").strip()
        cands = ([hero] if hero else []) + [self._member_app_id(e) for e in sp.get("members", [])]
        for c in cands:
            c = (c or "").strip()
            if c and c not in seen:
                seen.add(c)
                ids.append(c)
        snippet = (
            f"[tiers.{slug}]\n"
            f'description = "{sp.get("title") or slug} bundle (derived from the workspaces Space)"\n'
            f'extends = "core"\n'
            f"apps = [\n" + "".join(f'    "{i}",\n' for i in ids) + "]\n"
        )
        return {"slug": slug, "title": sp.get("title") or slug, "apps": ids, "tier_snippet": snippet}

    # ── Resolution ──────────────────────────────────────────────────────

    @staticmethod
    def _member_app_id(entry) -> str:
        if isinstance(entry, dict):
            return str(entry.get("app") or entry.get("id") or "")
        return str(entry or "")

    def _member_meta(self, entry, enabled: set[str], manifests: dict) -> dict:
        """Resolve one member entry (an app id string or an override dict with
        ``app`` + optional ``title`` / ``blurb`` / ``icon``) into display meta."""
        app_id = self._member_app_id(entry)
        ov_title = ov_blurb = ov_icon = None
        if isinstance(entry, dict):
            ov_title = entry.get("title")
            ov_blurb = entry.get("blurb") or entry.get("description")
            ov_icon = entry.get("icon")
        m = manifests.get(app_id)
        available = app_id in enabled and m is not None
        base_title = (m.name if m else app_id) or app_id
        base_desc = (m.description if m else "") or ""
        prefix = ((getattr(m, "provides", None) or {}).get("web") or {}).get("prefix") if m else None
        href = (prefix or f"/{app_id}").rstrip("/") + "/"
        return {
            "id": app_id,
            "title": ov_title or base_title,
            "description": ov_blurb or base_desc,
            "href": href,
            "icon": ov_icon or "",
            "available": available,
        }

    def _resolve_members(self, space: dict, enabled: set[str], manifests: dict) -> list[dict]:
        """Member meta list, excluding the space's hero_app (it's the hero,
        not a grid tile)."""
        hero = space.get("hero_app") or ""
        out = []
        for entry in space.get("members", []):
            if self._member_app_id(entry) == hero:
                continue
            out.append(self._member_meta(entry, enabled, manifests))
        return out

    def _space_payload(self, space: dict, enabled: set[str], manifests: dict) -> dict:
        members = self._resolve_members(space, enabled, manifests)
        return {
            "slug": space["slug"],
            "title": space.get("title") or space["slug"],
            "icon": space.get("icon") or "",
            "tagline": space.get("tagline") or "",
            "order": _as_int(space.get("order"), 100),
            "hero_app": space.get("hero_app") or "",
            "context": space.get("context") or {},
            "members": members,
            "member_count": len(members),
            "available_count": sum(1 for x in members if x["available"]),
            "user_created": bool(space.get("_user_created")),
            "edited": bool(space.get("_edited")),
        }

    async def list_spaces(self) -> list[dict]:
        enabled = set(self.kernel.apps.enabled_ids())
        manifests = self.kernel.apps.manifests
        reg = self._registry()
        out = []
        for space in reg.values():
            payload = self._space_payload(space, enabled, manifests)
            # Hide a default space whose members are all uninstalled (keeps a
            # fresh public clone clean). Config/manifest spaces always show.
            if space.get("_source") == "default" and payload["available_count"] == 0:
                continue
            out.append(payload)
        out.sort(key=lambda x: (x["order"], x["title"].lower()))
        return out

    async def list_members(self, slug: str) -> list[dict]:
        """Resolved member meta for a space — WITHOUT the hero. Cross-app
        callers (e.g. academy sourcing its practice grid from the
        english-academy Space) MUST use this, not get_space: get_space calls
        the hero_app's dashboard(), and the hero_app calling back into
        get_space would recurse. list_members never touches the hero."""
        reg = self._registry()
        space = reg.get(slug)
        if space is None:
            return []
        enabled = set(self.kernel.apps.enabled_ids())
        manifests = self.kernel.apps.manifests
        return self._resolve_members(space, enabled, manifests)

    # ── Space-scoped Ask ────────────────────────────────────────────

    def _space_has_ask(self, space: dict) -> bool:
        ctx = space.get("context") or {}
        return bool(ctx.get("kb_tags") or ctx.get("persona"))

    def _space_sources(self, kb_tags: list[str], question: str) -> list[dict]:
        """Top notes scoped to the Space's tags, ranked by keyword overlap with
        the question. Pure vault reads — no model call."""
        if not kb_tags:
            return []
        try:
            notes = self.vault_query(tags=kb_tags) or []
        except Exception:
            notes = []
        terms = {t for t in re.findall(r"\w+", question.lower()) if len(t) > 2}
        ranked = []
        for n in notes[:_ASK_SCAN]:
            path = n.get("path") or ""
            if not path:
                continue
            props = n.get("properties") or {}
            title = props.get("title") or n.get("name") or path
            hay = (str(title) + " " + path).lower()
            score = sum(hay.count(t) for t in terms)
            ranked.append((score, path, str(title)))
        # Keyword hits first; ties keep vault_query order (recency-ish).
        ranked.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _score, path, title in ranked[:_ASK_TOP]:
            try:
                body = self.vault_read_body(path) or ""
            except Exception:
                body = ""
            out.append({"path": path, "title": title, "excerpt": body[:_ASK_CHARS]})
        return out

    async def ask_space(self, slug: str, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            return {"error": "question is required"}
        space = self._registry().get(slug)
        if space is None:
            return {"error": "space not found", "slug": slug}
        if not self._space_has_ask(space):
            return {"error": "This Space has no ask context (no kb_tags configured)."}

        ctx = space.get("context") or {}
        kb_tags = ctx.get("kb_tags") or []
        domain = ctx.get("think_domain") or "text"
        persona = (ctx.get("persona") or "").strip()
        if not persona and ctx.get("companion"):
            persona = "Adopt a warm, encouraging tutor's tone."

        sources = self._space_sources(kb_tags, question)
        if sources:
            ctx_block = "\n\n".join(
                f"### {s['title']}\n{s['excerpt']}" for s in sources if s["excerpt"]
            )
        else:
            ctx_block = (
                "(No notes tagged "
                + ", ".join(kb_tags)
                + " were found in the vault.)"
            )
        system = ASK_SYSTEM.format(
            space=space.get("title") or slug,
            persona=(persona + "\n") if persona else "",
        )
        user = (
            f"Question: {question}\n\n"
            f"Context notes from the vault (tags: {', '.join(kb_tags) or 'none'}):\n\n"
            f"{ctx_block}"
        )
        try:
            answer = await self.think(user, domain=domain, system=system, temperature=0.3)
        except Exception as e:
            return {"error": f"think failed: {e}"}

        prov = self.last_provenance()
        await self.emit("workspaces:asked", {"slug": slug, "scoped": bool(sources)})
        return {
            "ok": True,
            "answer": (answer or "").strip(),
            "sources": [{"path": s["path"], "title": s["title"]} for s in sources],
            "scoped": bool(sources),
            "kb_tags": kb_tags,
            "provenance": prov,
        }

    # ── Live widgets (dynamic Space surfaces) ───────────────────────────
    # A Space may declare `widgets` rendered live on its detail page (vs the
    # static member link grid). Two types: `embed` (iframe a member app's page
    # at ?embed=1) and `panel` (call a member method, render via shared
    # renderers). Both feature-detect + fail soft per widget (hub-panel contract).

    async def _safe_call(self, app_id: str, method: str):
        try:
            return await self.call_app(app_id, method)
        except Exception as e:  # noqa: BLE001 — fail-soft per widget by design
            self.kernel.syslog.warn("workspaces", f"widget {app_id}.{method} failed: {e}")
            return None

    async def _resolve_widgets(self, space: dict, enabled: set[str], manifests: dict) -> list[dict]:
        raw = space.get("widgets") or []
        if not isinstance(raw, list):
            return []

        async def resolve_one(w):
            if not isinstance(w, dict):
                return None
            wtype = str(w.get("type") or "").strip()
            app_id = str(w.get("app") or "").strip()
            if not app_id:
                return None
            m = manifests.get(app_id)
            available = app_id in enabled and m is not None
            title = w.get("title") or (m.name if m else app_id)
            if wtype == "embed":
                prefix = ((getattr(m, "provides", None) or {}).get("web") or {}).get("prefix") if m else None
                href = (prefix or f"/{app_id}").rstrip("/") + "/"
                sep = "&" if "?" in href else "?"
                return {"type": "embed", "app": app_id, "title": title,
                        "src": f"{href}{sep}embed=1",
                        "height": _as_int(w.get("height"), 520), "available": available}
            if wtype == "panel":
                method = str(w.get("method") or "").strip()
                data = await self._safe_call(app_id, method) if (available and method) else None
                return {"type": "panel", "app": app_id, "title": title,
                        "renderer": str(w.get("renderer") or "stat-tile").strip(),
                        "data": data, "available": available}
            return None

        results = await asyncio.gather(*(resolve_one(w) for w in raw))
        return [r for r in results if r]

    async def get_space(self, slug: str) -> dict | None:
        reg = self._registry()
        space = reg.get(slug)
        if space is None:
            return None
        enabled = set(self.kernel.apps.enabled_ids())
        manifests = self.kernel.apps.manifests
        payload = self._space_payload(space, enabled, manifests)
        payload["widgets"] = await self._resolve_widgets(space, enabled, manifests)

        # Optional rich hero from the space's hero_app (e.g. academy.dashboard).
        hero = None
        hero_app = space.get("hero_app") or ""
        if hero_app and hero_app in enabled and hero_app in manifests:
            try:
                hero = await self.call_app(hero_app, "dashboard")
            except Exception:
                hero = None
        payload["hero"] = hero
        payload["has_ask"] = self._space_has_ask(space)
        return payload

    # ── Hub panel ───────────────────────────────────────────────────────

    async def panel_spaces(self):
        spaces = await self.list_spaces()
        if not spaces:
            return None
        return [
            {
                "title": f"{(s['icon'] + ' ') if s['icon'] else ''}{s['title']}",
                "subtitle": s["tagline"]
                or f"{s['available_count']} app(s)",
                "href": f"/workspaces/?space={s['slug']}",
            }
            for s in spaces
        ]

    # ── CLI ─────────────────────────────────────────────────────────────

    @cli_command("workspaces", help="List curated workspaces (Spaces)")
    async def cmd_workspaces(self):
        spaces = await self.list_spaces()
        if not spaces:
            print("  No spaces configured.")
            return
        for s in spaces:
            icon = f"{s['icon']} " if s["icon"] else ""
            print(
                f"  {icon}{s['title']} — "
                f"{s['available_count']}/{s['member_count']} apps  "
                f"(/workspaces/?space={s['slug']})"
            )

    # ── Web ─────────────────────────────────────────────────────────────

    @web_route("GET", "/api/spaces")
    async def api_spaces(self, request):
        return {"spaces": await self.list_spaces()}

    @web_route("GET", "/api/spaces/{slug}")
    async def api_space(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        space = await self.get_space(slug)
        if space is None:
            return {"error": "space not found", "slug": slug}
        return space

    @web_route("POST", "/api/spaces/{slug}/ask")
    async def api_ask(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        question = (body or {}).get("question", "")
        return await self.ask_space(slug, question)

    # ── Membership editor routes ─────────────────────────────────────────

    @web_route("GET", "/api/apps-available")
    async def api_apps_available(self, request):
        return {"apps": self.installable_apps()}

    @web_route("POST", "/api/spaces")
    async def api_create_space(self, request):
        b = await self.safe_json(request) or {}
        return self.create_space(b.get("slug", ""), b.get("title", ""),
                                 b.get("icon", ""), b.get("tagline", ""))

    @web_route("PUT", "/api/spaces/{slug}/members")
    async def api_set_members(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        b = await self.safe_json(request) or {}
        ids = b.get("members")
        if not isinstance(ids, list):
            return {"error": "members must be a list"}
        return self.set_members(slug, ids)

    @web_route("POST", "/api/spaces/{slug}/members")
    async def api_add_member(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        b = await self.safe_json(request) or {}
        return self.add_member(slug, b.get("app", ""))

    @web_route("DELETE", "/api/spaces/{slug}/members/{app_id}")
    async def api_remove_member(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        app_id = (request.path_params.get("app_id") or "").strip()
        return self.remove_member(slug, app_id)

    @web_route("DELETE", "/api/spaces/{slug}")
    async def api_delete_space(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        return self.delete_space(slug)

    @web_route("GET", "/api/spaces/{slug}/bundle")
    async def api_bundle(self, request):
        slug = (request.path_params.get("slug") or "").strip()
        d = self.derive_bundle(slug)
        return d if d is not None else {"error": "space not found", "slug": slug}


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
