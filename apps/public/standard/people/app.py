"""People — unified person directory (roster + capacity + relationships).

Owns the Person entity (one `.md` per person, tagged `person`, in
`30_Resources/People`). Other apps declare work assignments via
`self.emit_assignment(person_id, item, weight_hours, role)` on BaseApp; we
aggregate into a live workload index.

Responsibilities:
  1. Roster + capacity — who's on the team, who's overloaded right now.
  2. Workload breakdown — what each person is doing, segmented by source
     app (projects vs boards vs ...) and by role.
  3. Skills match — "who on my team can do X" and who's got headroom.
  4. Relationships — frequency, trust, energy, birthdays, last-contact.
  5. Interactions — ``## Quick Log`` section, overdue detection.
  6. AI over relationships — suggest who to reach out to, persona sketches, RAG chat.

Absorbed the former `contacts` app — both read the same vault notes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    VaultModel,
    bool_validator,
    cli_command,
    float_or_none_validator,
    parse_frontmatter,
    web_route,
)
from emptyos.sdk.utils import slugify

from . import ai_surfaces as _ai_surfaces
from . import engagement as _engagement
from . import workload as _workload
from .shared import (
    AI_SUGGEST_FORMAT,
    AI_SUGGEST_SYSTEM,
    CHAT_SYSTEM,
    PERSONA_SYSTEM,
    _DEFAULT_ROLE,
    _FILENAME_PREFIX,
)

from . import simulate as _sim
from . import compose as _compose_routes

_PERSON_TAG = "person"

_FREQUENCY_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}


def _load_ratio(load_hours: float, capacity: float) -> float:
    if capacity <= 0:
        return 0.0
    return round(load_hours / capacity, 3)


def _band(ratio: float) -> str:
    if ratio >= 1.0:
        return "overloaded"
    if ratio >= 0.7:
        return "busy"
    return "ok"


class Person(VaultModel):
    """Soft typed contract for a `person` note's frontmatter.

    The single home for write-boundary coercion across every write path
    (`set_field`, `api_update_person`): a boards cell edit of `active=0`
    (falsy int) lands as `False`, `"30"` as `30.0`. `extra="allow"` keeps
    unknown / body-managed fields intact; legacy hyphen keys fold into the
    underscore shape on read. All fields are optional — vault notes are
    tolerant, and `name` may live only in the filename.
    """

    TAG = _PERSON_TAG

    name: str = ""
    role: str = ""
    type: str = "internal"
    capacity_hours_per_week: float | None = None
    active: bool = True
    relationship: str = ""
    company: str = ""
    trust_level: str = ""
    energy: str = ""
    contact_frequency: str = ""
    last_contact: str = ""
    phone: str = ""
    email: str = ""
    birthday: str = ""
    # skills / focus_areas are not declared: they're list-shaped and edited via
    # the app form (never a boards cell), so they ride through as `extra="allow"`
    # passthrough — keeping a malformed list value from failing the whole parse.

    _coerce_active = bool_validator("active")
    _coerce_capacity = float_or_none_validator("capacity_hours_per_week")

    @classmethod
    def _legacy_aliases(cls, raw: dict) -> dict:
        for new, old in (
            ("trust_level", "trust-level"),
            ("contact_frequency", "contact-frequency"),
            ("last_contact", "last-contact"),
        ):
            if not raw.get(new) and raw.get(old) is not None:
                raw[new] = raw[old]
        return raw

    @classmethod
    def settable_fields(cls) -> set[str]:
        # Boards-settable scalars only — lists (skills/focus_areas) and
        # identity (id) are edited via the app form, not kanban cells.
        return {
            "name", "role", "type", "capacity_hours_per_week", "active",
            "relationship", "company", "trust_level", "energy",
            "contact_frequency", "last_contact", "phone", "email", "birthday",
        }


class PeopleApp(BaseApp):

    async def setup(self):
        await super().setup()
        self._assignments: dict[tuple, dict] = {}
        self._rebuilt_at: str = ""
        self.kernel.events.on("people:assigned", self._on_assigned)
        self.kernel.events.on("people:unassigned", self._on_unassigned)
        import asyncio

        self.spawn_background(self._rebuild_index())

    async def _rebuild_index(self):
        self._assignments.clear()
        for app_id, instance in self.kernel.apps.instances.items():
            if app_id == self.manifest.id:
                continue
            try:
                rows = await instance.list_assignments()
            except Exception as e:
                self.log_warn(f"list_assignments failed for {app_id}: {e}")
                continue
            for row in rows or []:
                self._record(row)
        self._rebuilt_at = datetime.now().isoformat()

    def _record(self, row: dict):
        person = row.get("person")
        item = row.get("item") or {}
        if not person or not item.get("app") or not item.get("id"):
            return
        role = row.get("role", _DEFAULT_ROLE)
        key = (person, item["app"], item["id"], role)
        self._assignments[key] = {
            "person": person,
            "item": item,
            "weight_hours": float(row.get("weight_hours", 1.0) or 0),
            "role": role,
        }

    async def _on_assigned(self, event):
        self._record(getattr(event, "data", None) or {})

    async def _on_unassigned(self, event):
        data = getattr(event, "data", None) or {}
        item = data.get("item") or {}
        role = data.get("role", _DEFAULT_ROLE)
        key = (data.get("person"), item.get("app"), item.get("id"), role)
        self._assignments.pop(key, None)

    def _folder(self) -> str:
        return self.setting(
            "people.folder", "30_Resources/People"
        )  # default fallback; overridden via settings or vault_config

    def _people_dir(self) -> Path:
        """Absolute path to the people folder; Path(".") if vault unmounted."""
        return self.vault_config_path("people_dir", self._folder()) or Path(".")

    def _default_capacity(self) -> float:
        try:
            return float(self.setting("people.default_capacity_hours", 40))
        except (TypeError, ValueError):
            return 40.0

    def _parse_frontmatter(self, content: str) -> dict:
        return parse_frontmatter(content) or {}

    def _serialize_frontmatter(self, fm: dict) -> str:
        lines = ["---"]
        for k, v in fm.items():
            if v is None or v == "":
                continue
            if isinstance(v, list):
                sv = "[" + ", ".join(str(x) for x in v) + "]"
            else:
                sv = str(v)
                if any(c in sv for c in ":#{}[]|>&*?!,"):
                    sv = f'"{sv}"'
            lines.append(f"{k}: {sv}")
        lines.append("---")
        return "\n".join(lines)

    def _parse_quick_log(self, content: str) -> list[dict]:
        entries = []
        in_log = False
        for line in content.split("\n"):
            if line.strip().startswith("## Quick Log"):
                in_log = True
                continue
            if in_log and line.startswith("## "):
                break
            if in_log and line.strip().startswith("- "):
                m = re.match(r"^- (\d{4}-\d{2}-\d{2}):\s*(.+)", line.strip())
                if m:
                    entries.append({"date": m.group(1), "text": m.group(2).strip()})
        return entries

    def _people_notes(self) -> list[dict]:
        return self.vault_query(tags=[_PERSON_TAG]) or []

    def _shape_person(self, note: dict) -> dict:
        """Merge work fields (capacity/skills/role) and social fields
        (relationship/trust/energy/birthday/frequency) into one record."""
        props = note.get("properties") or {}
        raw_name = note.get("name", "")
        pid = (
            props.get("id")
            or raw_name.replace(_FILENAME_PREFIX, "").lower()
            or (slugify(props.get("name", "")) or "person")
        )
        name = props.get("name") or raw_name.replace(_FILENAME_PREFIX, "").replace("-", " ") or pid

        # Typed view over the frontmatter. The model is the one place that
        # coerces YAML's string-everything reality — critically, `active`:
        # the vault parser stores booleans as the strings "True"/"False", so a
        # raw `bool(props["active"])` reads "False" as truthy. `coerce_bool`
        # fixes that. Legacy hyphen keys (trust-level, contact-frequency,
        # last-contact) fold in via the model's `_legacy_aliases`. Fail-soft
        # to defaults if a note can't be parsed at all.
        p = Person.from_query_row(note) or Person()

        capacity = p.capacity_hours_per_week
        if capacity is None:
            capacity = self._default_capacity() if (p.type or "internal") == "internal" else 0

        record = {
            # Core
            "id": pid,
            "name": p.name or name,
            "path": note.get("path", ""),
            "file": raw_name,
            "active": p.active,
            # Work
            "role": p.role,
            "type": p.type or "internal",
            "capacity_hours_per_week": float(capacity or 0),
            "skills": props.get("skills", []) or [],
            "focus_areas": props.get("focus_areas", []) or [],
            # Relationship / social
            "relationship": p.relationship,
            "company": p.company,
            "trust_level": p.trust_level,
            "energy": p.energy,
            "contact_frequency": p.contact_frequency,
            "last_contact": p.last_contact,
            "phone": p.phone,
            "email": p.email,
            "birthday": p.birthday,
        }
        return record

    def _find_note(self, ident: str) -> dict | None:
        """Find a note by id OR by name (case/space/dash-tolerant)."""
        target = (ident or "").strip().lower().replace("-", " ").replace("_", " ")
        for n in self._people_notes():
            props = n.get("properties") or {}
            pid = (props.get("id") or (n.get("name") or "").replace(_FILENAME_PREFIX, "")).lower()
            name = (props.get("name") or "").lower()
            fname = (
                (n.get("name", "") or "").replace(_FILENAME_PREFIX, "").replace("-", " ").lower()
            )
            if target == pid or target == name.replace("-", " ") or target == fname:
                return n
        return None

    def _find_file(self, ident: str) -> Path | None:
        """Resolve ``ident`` to an absolute vault file path."""
        note = self._find_note(ident)
        if note:
            path = note.get("path")
            if path:
                p = Path(path)
                if p.exists():
                    return p
        # Fallback: glob the people dir for @Name.md
        people_dir = self._people_dir()
        if not people_dir or not people_dir.exists():
            return None
        normalised = (ident or "").lower().replace("-", " ").replace("_", " ").strip()
        for f in people_dir.glob(f"{_FILENAME_PREFIX}*.md"):
            if f.stem.lstrip(_FILENAME_PREFIX).replace("-", " ").lower() == normalised:
                return f
        for f in people_dir.glob("*.md"):
            if f.stem.lower() == normalised:
                return f
        return None

    async def person_path(self, id: str = "") -> str:
        """Vault-relative path of a person's note.

        Wired into the 4D timeline contract — see [provides.timeline]
        entity_source in manifest.toml. Uses `_find_note` (index-backed,
        slug-tolerant) rather than `_find_file` whose glob normalises
        hyphens to spaces and silently misses dash-slug files like
        `ines-carvalho.md`.
        """
        n = self._find_note(id)
        if n and n.get("path"):
            return str(n["path"]).replace("\\", "/")
        return ""

    def _days_since(self, date_str: str) -> int | None:
        if not date_str:
            return None
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            return (date.today() - d).days
        except (ValueError, TypeError):
            return None

    def _health_score(self, person: dict) -> int:
        score = 50
        try:
            trust = int(str(person.get("trust_level", "") or "0"))
        except (ValueError, TypeError):
            trust = 0
        score += trust * 5
        days = self._days_since(person.get("last_contact", ""))
        if days is not None:
            if days <= 7:
                score += 20
            elif days <= 30:
                score += 10
            elif days <= 90:
                score -= 10
            else:
                score -= 25
        energy = (person.get("energy", "") or "neutral").lower()
        if energy == "gives":
            score += 10
        elif energy == "drains":
            score -= 10
        return max(0, min(100, score))

    def _frequency_days(self, freq) -> int | None:
        s = str(freq).lower().strip() if freq else ""
        return _FREQUENCY_DAYS.get(s) if s else None

    def _load_for_person(self, person_id: str) -> list[dict]:
        return [a for a in self._assignments.values() if a["person"] == person_id]

    def _person_with_load(self, shape: dict) -> dict:
        rows = self._load_for_person(shape["id"])
        load_hours = sum(r["weight_hours"] for r in rows)
        cap = shape["capacity_hours_per_week"]
        ratio = _load_ratio(load_hours, cap) if shape["type"] == "internal" else 0.0
        return {
            **shape,
            "load_hours": round(load_hours, 2),
            "load_ratio": ratio,
            "band": _band(ratio),
            "assignment_count": len(rows),
        }

    def _enrich(self, person: dict) -> dict:
        person["health_score"] = self._health_score(person)
        person["days_since"] = self._days_since(person.get("last_contact", ""))
        return person

    # Personal relationships with no explicit cadence go "cold" after this many
    # days since last contact — caught by the reach-out nudge even when the user
    # never set a contact_frequency.
    _COLD_DAYS = 60

    async def list_people(self, active_only: bool = True) -> list[dict]:
        out = []
        for n in self._people_notes():
            p = self._shape_person(n)
            if active_only and not p["active"]:
                continue
            out.append(self._enrich(self._person_with_load(p)))
        return sorted(out, key=lambda p: (-p["load_ratio"], p["name"]))

    async def list_contacts(self) -> list[dict]:
        """Back-compat alias for callers that still use the contacts name."""
        return await self.list_people(active_only=True)

    async def get_person(self, id: str) -> dict | None:
        n = self._find_note(id)
        if not n:
            return None
        return self._enrich(self._person_with_load(self._shape_person(n)))

    async def search_people(self, query: str) -> list[dict]:
        q = (query or "").lower().strip()
        if not q:
            return await self.list_people()
        results = []
        for p in await self.list_people():
            hay = " ".join(
                str(p.get(f, "") or "")
                for f in ("name", "role", "relationship", "company", "email")
            ).lower()
            if q in hay:
                results.append(p)
        return results

    # ── Boards view-layer integration ──
    # Derived from the Person contract — no hand-maintained whitelist drift.
    SETTABLE_FIELDS = Person.settable_fields()

    def _coerce_person_fields(self, updates: dict) -> dict:
        """Normalize update values through the Person contract so writes land
        typed (a boards cell's `active=0` → `False`, `"30"` → `30.0`). Coerces
        only the delta, so an unrelated malformed field on the note can't block
        it. Fail-soft: if the contract can't parse, write the raw values."""
        try:
            inst = Person.from_query_row({"properties": dict(updates)})
        except Exception:
            return updates
        if inst is None:
            return updates
        dumped = inst.model_dump(mode="json")
        return {k: dumped.get(k, updates[k]) for k in updates}

    async def list_all(self) -> list[dict]:
        """Flat list shape consumed by boards when source.type == 'app'.
        Returns all people (including inactive) so kanban can group by relationship/band."""
        rows = []
        for n in self._people_notes():
            p = self._enrich(self._person_with_load(self._shape_person(n)))
            rows.append(
                {
                    "id": p["id"],
                    "name": p.get("name", ""),
                    "role": p.get("role", ""),
                    "type": p.get("type", ""),
                    "company": p.get("company", ""),
                    "relationship": p.get("relationship", ""),
                    "energy": p.get("energy", ""),
                    "trust_level": p.get("trust_level", ""),
                    "contact_frequency": p.get("contact_frequency", ""),
                    "last_contact": p.get("last_contact", ""),
                    "capacity_hours_per_week": p.get("capacity_hours_per_week", 0),
                    "load_ratio": p.get("load_ratio", 0),
                    "band": p.get("band", "ok"),
                    "active": p.get("active", True),
                    "phone": p.get("phone", ""),
                    "email": p.get("email", ""),
                    "birthday": p.get("birthday", ""),
                }
            )
        return rows

    async def set_field(self, id: str, field: str, value) -> dict:
        """Cross-app setter for the boards view layer. Mirrors api_update_person's whitelist."""
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        n = self._find_note(id)
        if not n:
            return {"error": "Person not found"}
        self.vault_update(n["path"], self._coerce_person_fields({field: value}))
        await self.emit("people:updated", {"id": id, "field": field, "value": value})
        return {"ok": True}

    @web_route("GET", "/api/people")
    async def api_list_people(self, request):
        active_only = request.query_params.get("active_only", "1") != "0"
        return await self.list_people(active_only=active_only)

    @web_route("GET", "/api/list")
    async def api_list_alias(self, request):
        return await self.list_people(active_only=True)

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        query = request.query_params.get("q", "")
        return await self.search_people(query)

    @web_route("GET", "/api/people/{id}")
    async def api_get_person(self, request):
        pid = request.path_params.get("id", "")
        person = await self.get_person(pid)
        if not person:
            return {"error": "Person not found"}
        return person

    @web_route("POST", "/api/rebuild")
    async def api_rebuild(self, request):
        await self._rebuild_index()
        return {"ok": True, "assignments": len(self._assignments), "rebuilt_at": self._rebuilt_at}

    @web_route("POST", "/api/people")
    async def api_create_person(self, request):
        data = await request.json()
        return await self.create_person(**data)

    async def create_person(self, name: str = "", **data) -> dict:
        """Kwargs-only create for cross-app callers (call_app is kwargs-only).

        Same behaviour as POST /api/people; boards' Planner importer is the
        first cross-app consumer (create-or-match imported assignees).
        """
        name = (name or "").strip()
        if not name:
            return {"error": "name required"}
        pid = data.get("id") or (slugify(name) or "person")
        if self._find_note(pid):
            return {"error": f"person '{pid}' already exists"}
        folder = self._folder()
        rel_path = f"{folder}/{pid}.md"
        fm = {
            "tags": [_PERSON_TAG],
            "id": pid,
            "name": name,
            "role": data.get("role", ""),
            "type": data.get("type", "internal"),
            "capacity_hours_per_week": float(
                data.get("capacity_hours_per_week", self._default_capacity())
            ),
            "skills": data.get("skills", []) or [],
            "focus_areas": data.get("focus_areas", []) or [],
            "active": bool(data.get("active", True)),
        }
        for field in (
            "relationship",
            "company",
            "trust_level",
            "energy",
            "contact_frequency",
            "phone",
            "email",
            "birthday",
        ):
            if data.get(field):
                fm[field] = data[field]
        self.vault_create_note(rel_path, fm, body=data.get("body", ""))
        await self.emit("people:created", {"id": pid, "name": name})
        # Back-compat for reactor wiring on the old contacts events.
        await self.emit("contacts:created", {"name": name, "file": f"{pid}.md"})
        return {"ok": True, "id": pid, "path": rel_path}

    @web_route("PATCH", "/api/people/{id}")
    async def api_update_person(self, request):
        pid = request.path_params.get("id", "")
        data = await request.json()
        n = self._find_note(pid)
        if not n:
            return {"error": "Person not found"}
        allowed = {
            "name",
            "role",
            "type",
            "capacity_hours_per_week",
            "skills",
            "focus_areas",
            "active",
            "relationship",
            "company",
            "trust_level",
            "energy",
            "contact_frequency",
            "last_contact",
            "phone",
            "email",
            "birthday",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return {"error": "No valid fields"}
        self.vault_update(n["path"], self._coerce_person_fields(updates))
        await self.emit("people:updated", {"id": pid, "updates": updates})
        await self.emit(
            "contacts:edited",
            {"name": n.get("properties", {}).get("name", pid), "fields": list(updates.keys())},
        )
        return {"ok": True}

    @web_route("DELETE", "/api/people/{id}")
    async def api_archive_person(self, request):
        pid = request.path_params.get("id", "")
        n = self._find_note(pid)
        if not n:
            return {"error": "Person not found"}
        self.vault_update(n["path"], {"active": False})
        await self.emit("people:archived", {"id": pid})
        return {"ok": True}

    async def log_interaction(self, person_id: str, text: str, source: str = "") -> dict:
        """Append a Quick Log entry on a person's note + bump last_contact.
        Cross-app callable via ``call_app("people", "log_interaction", ...)``.
        """
        text = (text or "").strip()
        if not text:
            return {"error": "text required"}
        target = self._find_file(person_id)
        if not target or not target.exists():
            return {"error": "Person not found"}
        today = date.today().isoformat()
        prefix = f"[{source}] " if source else ""
        entry = f"- {today}: {prefix}{text}"
        async with self.note_lock(target):
            content = await self.read(str(target))
            if "## Quick Log" in content:
                idx = content.index("## Quick Log")
                end_of_line = content.index("\n", idx)
                content = content[: end_of_line + 1] + entry + "\n" + content[end_of_line + 1 :]
            else:
                content = content.rstrip() + "\n\n## Quick Log\n" + entry + "\n"
            # Update last_contact in frontmatter
            if content.startswith("---"):
                fm_end = content.find("---", 3)
                if fm_end > 0:
                    fm_block = content[3:fm_end]
                    if "last_contact:" in fm_block or "last-contact:" in fm_block:
                        fm_block = re.sub(r"(last[_-]contact:\s*).*", f"\\g<1>{today}", fm_block)
                    else:
                        fm_block = fm_block.rstrip() + f"\nlast_contact: {today}\n"
                    content = "---" + fm_block + content[fm_end:]
            await self.write(str(target), content)
        display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
        await self.emit("people:logged", {"id": person_id, "name": display_name, "text": text})
        await self.emit("contacts:logged", {"name": display_name, "text": text})
        return {"ok": True, "entry": entry}

    async def voice_lookup(self, query: str = "") -> dict:
        """Voice verb — find a person and show their card."""
        q = (query or "").strip()
        if not q:
            return {"say": "Who are you looking for?"}
        try:
            matches = await self.search_people(q)
        except Exception:
            matches = []
        if not matches:
            return {"say": f"I couldn't find anyone matching '{q}'."}
        p = matches[0]
        name = p.get("name", q)
        fields = []
        for label, key in (("Role", "role"), ("Company", "company"),
                           ("Relationship", "relationship"), ("Last contact", "last_contact")):
            v = p.get(key)
            if v:
                fields.append({"label": label, "value": v})
        if p.get("days_since") is not None:
            fields.append({"label": "Days since", "value": p["days_since"]})
        say = name + (f", {p['role']}" if p.get("role") else "")
        say += f" at {p['company']}" if p.get("company") else ""
        say += (f". Last contact {p['last_contact']}." if p.get("last_contact") else ".")
        return {
            "say": say,
            "card": {"renderer": "entity-card", "title": "Person",
                     "data": {"title": name, "subtitle": p.get("role", ""), "fields": fields}},
            "link": {"text": f"Open {name}", "href": "/people/"},
        }

    async def voice_log_interaction(self, person: str = "", text: str = "") -> dict:
        """Voice verb — log an interaction; asks for the text via a form when missing."""
        person = (person or "").strip()
        if not person:
            return {"say": "Who did you interact with?"}
        text = (text or "").strip()
        if not text:
            # Propose-not-autofill: render a form asking for the interaction text.
            return {
                "say": f"What should I log for {person}?",
                "card": {
                    "renderer": "form",
                    "title": f"Log an interaction · {person}",
                    "data": {
                        "fields": [{"name": "text", "label": "What happened?",
                                    "type": "text", "required": True}],
                        "submit": {"label": "Log it", "verb": "people.log_interaction",
                                   "args": {"person": person}},
                    },
                },
            }
        res = await self.log_interaction(person, text, source="voice")
        if res.get("error"):
            return {"say": f"Couldn't log that — {res['error']}."}
        return {"say": f"Logged for {person}.",
                "link": {"text": f"Open {person}", "href": "/people/"}}

    @web_route("POST", "/api/people/{id}/log")
    async def api_log_interaction(self, request):
        ident = request.path_params.get("id", "")
        data = await request.json()
        return await self.log_interaction(ident, data.get("text", ""), data.get("source", ""))

    @web_route("POST", "/api/edit/{id}")
    async def api_edit_fields(self, request):
        """Free-form frontmatter edit (does not validate against the allowed
        list in PATCH /api/people/{id}). Used by the relationship form."""
        ident = request.path_params.get("id", "")
        target = self._find_file(ident)
        if not target:
            return {"error": "Person not found"}
        data = await request.json()
        fields = data.get("fields", {})
        if not fields:
            return {"error": "fields dict required"}
        async with self.note_lock(target):
            content = await self.read(str(target))
            fm = self._parse_frontmatter(content)
            updated = []
            for k, v in fields.items():
                old = fm.get(k)
                fm[k] = str(v) if not isinstance(v, list) else v
                updated.append({"field": k, "old": old, "new": fm[k]})
            body = content
            if body.startswith("---"):
                end = body.find("---", 3)
                if end > 0:
                    body = body[end + 3 :]
            new_content = self._serialize_frontmatter(fm) + body
            await self.write(str(target), new_content)
        display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
        await self.emit("people:updated", {"id": ident, "updates": fields})
        await self.emit("contacts:edited", {"name": display_name, "fields": list(fields.keys())})
        return {"ok": True, "updated": updated}

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        people = await self.list_people()
        overdue = self._get_overdue(people)
        avg_health = round(sum(p["health_score"] for p in people) / len(people)) if people else 0
        overloaded = [p for p in people if p["band"] == "overloaded"]
        by_rel: dict[str, int] = {}
        by_energy: dict[str, int] = {}
        for p in people:
            r = p.get("relationship", "unknown") or "unknown"
            by_rel[r] = by_rel.get(r, 0) + 1
            e = p.get("energy", "unknown") or "unknown"
            by_energy[e] = by_energy.get(e, 0) + 1
        return {
            "total": len(people),
            "overloaded": len(overloaded),
            "overdue": len(overdue),
            "avg_health": avg_health,
            "by_relationship": by_rel,
            "by_energy": by_energy,
        }

    _extract_body = _sim._extract_body

    _me_file = _sim._me_file

    _load_user_profile = _sim._load_user_profile

    api_simulate = _sim.api_simulate

    api_enrich_save = _sim.api_enrich_save

    api_chat_archive = _sim.api_chat_archive

    _extract_table_rows = _sim._extract_table_rows

    _extract_section_bullets = _sim._extract_section_bullets

    _strip_bold = _sim._strip_bold

    api_personality = _sim.api_personality

    # ── Compose (extracted to compose.py) ──
    api_compose_kinds = _compose_routes.api_compose_kinds

    api_compose       = _compose_routes.api_compose

    api_compose_log   = _compose_routes.api_compose_log

    _compose_context  = _compose_routes._compose_context

    _voice_rules      = _compose_routes._voice_rules

    api_values = _sim.api_values

    @cli_command("people", help="Manage people + capacity + relationships")
    async def cmd_people(self, action: str = "list", query: str = ""):
        if action == "list":
            roster = await self.list_people()
            if not roster:
                print("  (no people yet)")
                return
            for p in roster:
                bar = f"{p['load_ratio'] * 100:.0f}%"
                print(f"  {p['name']:<25} {p['role']:<20} {bar:<6} {p['band']}")
        elif action == "rebuild":
            await self._rebuild_index()
            print(f"  rebuilt index — {len(self._assignments)} assignments tracked")
        elif action == "due":
            due = self._get_overdue(await self.list_people())
            if not due:
                print("  No overdue contacts")
                return
            for p in due:
                print(f"  {p['name']:<25} {p['days_overdue']}d overdue")
        elif action == "search" and query:
            results = await self.search_people(query)
            for p in results:
                print(f"  {p['name']} ({p.get('company', '')})")
            if not results:
                print(f"  No people matching '{query}'")

    # ── Ai Surfaces (extracted to ai_surfaces.py) ──
    api_profile    = _ai_surfaces.api_profile
    api_ai_suggest = _ai_surfaces.api_ai_suggest
    api_chat       = _ai_surfaces.api_chat
    api_persona    = _ai_surfaces.api_persona

    # ── Engagement (extracted to engagement.py) ──
    _get_overdue          = _engagement._get_overdue
    _reach_out_candidates = _engagement._reach_out_candidates
    birthdays             = _engagement.birthdays
    api_birthdays         = _engagement.api_birthdays
    api_due               = _engagement.api_due
    api_frequency         = _engagement.api_frequency
    api_notifications     = _engagement.api_notifications
    scheduled_birthday_push = _engagement.scheduled_birthday_push
    panel_reach_out       = _engagement.panel_reach_out
    panel_birthdays       = _engagement.panel_birthdays

    # ── Workload (extracted to workload.py) ──
    match               = _workload.match
    api_match           = _workload.api_match
    api_person_workload = _workload.api_person_workload
    api_workload        = _workload.api_workload
    panel_overloaded    = _workload.panel_overloaded
