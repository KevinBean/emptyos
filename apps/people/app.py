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

from emptyos.sdk import BaseApp, cli_command, parse_frontmatter, parse_llm_json, web_route
from emptyos.sdk.utils import slugify

from . import simulate as _sim

_PERSON_TAG = "person"
_DEFAULT_ROLE = "assignee"
_FILENAME_PREFIX = "@"  # legacy contacts convention; new notes use id-based names

_FREQUENCY_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}


AI_SUGGEST_SYSTEM = (
    "You are a relationship coach. Given a contact list and overdue flags, "
    "pick exactly 3 people the user should reach out to this week.\n"
    "Weight: overdue + low-recent-contact + energy='gives' + higher trust_level.\n"
    "Do NOT: pick three overdue people if that ignores energy/trust; invent "
    "names not in the list; return more or fewer than 3; wrap JSON in markdown "
    "fences; add commentary before/after the array."
)

AI_SUGGEST_FORMAT = (
    "Return a JSON array of exactly 3 objects:\n"
    '[{"name": "exact name from list", "action": "call/message/coffee/meal", '
    '"reason": "1 sentence why now", "opener": "suggested opening message"}]\n'
    "Return ONLY valid JSON."
)

CHAT_SYSTEM = (
    "You are a personal relationship assistant. Answer the user's question "
    "about a specific person based ONLY on the information provided. "
    "If the info doesn't contain the answer, say so plainly.\n"
    "Do NOT: invent facts about the person; speculate about their inner state "
    "beyond what the notes support; answer as if you are the person."
)

PERSONA_SYSTEM = (
    "You describe a person's personality in 2-3 sentences based only on "
    "recorded interactions and notes about them. Focus on communication "
    "style, values, and relationship dynamics.\n"
    "Do NOT: use clinical labels (narcissist/introvert/etc.); speculate "
    "about childhood or trauma; flatter; exceed 3 sentences."
)


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


class PeopleApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._assignments: dict[tuple, dict] = {}
        self._rebuilt_at: str = ""
        self.kernel.events.on("people:assigned", self._on_assigned)
        self.kernel.events.on("people:unassigned", self._on_unassigned)
        import asyncio

        asyncio.create_task(self._rebuild_index())

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

    # ── Folder / settings helpers ──────────────────────────────────────

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

    # ── Frontmatter helpers (shared with simulate mixin) ───────────────

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

    # ── Vault reads ────────────────────────────────────────────────────

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

        capacity = props.get("capacity_hours_per_week")
        if capacity is None:
            capacity = (
                self._default_capacity() if props.get("type", "internal") == "internal" else 0
            )

        record = {
            # Core
            "id": pid,
            "name": name,
            "path": note.get("path", ""),
            "file": raw_name,
            "active": bool(props.get("active", True)),
            # Work
            "role": props.get("role", ""),
            "type": props.get("type", "internal"),
            "capacity_hours_per_week": float(capacity or 0),
            "skills": props.get("skills", []) or [],
            "focus_areas": props.get("focus_areas", []) or [],
            # Relationship / social
            "relationship": props.get("relationship", ""),
            "company": props.get("company", ""),
            "trust_level": props.get("trust_level", props.get("trust-level", "")),
            "energy": props.get("energy", ""),
            "contact_frequency": props.get("contact_frequency", props.get("contact-frequency", "")),
            "last_contact": props.get("last_contact", props.get("last-contact", "")),
            "phone": props.get("phone", ""),
            "email": props.get("email", ""),
            "birthday": props.get("birthday", ""),
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

    # ── Computed fields (enrichment) ───────────────────────────────────

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

    def _get_overdue(self, people: list[dict]) -> list[dict]:
        overdue = []
        for p in people:
            freq_days = self._frequency_days(p.get("contact_frequency", ""))
            if freq_days is None:
                continue
            days = p.get("days_since")
            if days is None:
                overdue.append({**p, "days_overdue": freq_days, "overdue_ratio": 2.0})
                continue
            threshold = freq_days * 1.5
            if days > threshold:
                overdue.append(
                    {
                        **p,
                        "days_overdue": int(days - freq_days),
                        "overdue_ratio": round(days / freq_days, 1),
                    }
                )
        overdue.sort(key=lambda x: -x["overdue_ratio"])
        return overdue

    # ── Public methods (called via call_app) ───────────────────────────

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

    async def match(self, skills: list[str], need_hours: float = 0) -> list[dict]:
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        want = {s.lower() for s in (skills or [])}
        candidates = await self.list_people(active_only=True)
        ranked = []
        for p in candidates:
            have = {s.lower() for s in p.get("skills", [])}
            overlap = len(want & have)
            penalty = max(0.0, p["load_ratio"] - 0.8)
            score = overlap - penalty
            if overlap == 0 and want:
                continue
            ranked.append(
                {
                    **p,
                    "overlap": overlap,
                    "overlap_pct": round(overlap / len(want) * 100, 1) if want else 0,
                    "score": round(score, 3),
                }
            )
        ranked.sort(key=lambda r: (-r["score"], -r["overlap"], r["load_ratio"], r["name"]))
        return ranked

    # ── Boards view-layer integration ──
    SETTABLE_FIELDS = {
        "name",
        "role",
        "type",
        "capacity_hours_per_week",
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
        self.vault_update(n["path"], {field: value})
        await self.emit("people:updated", {"id": id, "field": field, "value": value})
        return {"ok": True}

    # ── Web routes — roster / capacity / matching ──────────────────────

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

    @web_route("GET", "/api/people/{id}/workload")
    async def api_person_workload(self, request):
        pid = request.path_params.get("id", "")
        person = await self.get_person(pid)
        if not person:
            return {"error": "Person not found"}
        rows = self._load_for_person(pid)
        by_app: dict[str, float] = {}
        by_role: dict[str, float] = {}
        for r in rows:
            by_app[r["item"].get("app", "?")] = (
                by_app.get(r["item"].get("app", "?"), 0) + r["weight_hours"]
            )
            by_role[r.get("role", _DEFAULT_ROLE)] = (
                by_role.get(r.get("role", _DEFAULT_ROLE), 0) + r["weight_hours"]
            )
        return {
            "person": person,
            "assignments": rows,
            "by_app": {k: round(v, 2) for k, v in by_app.items()},
            "by_role": {k: round(v, 2) for k, v in by_role.items()},
        }

    @web_route("GET", "/api/workload")
    async def api_workload(self, request):
        return await self.list_people(active_only=True)

    @web_route("POST", "/api/rebuild")
    async def api_rebuild(self, request):
        await self._rebuild_index()
        return {"ok": True, "assignments": len(self._assignments), "rebuilt_at": self._rebuilt_at}

    @web_route("GET", "/api/match")
    async def api_match(self, request):
        skills_q = request.query_params.get("skills", "")
        skills = [s.strip() for s in skills_q.split(",") if s.strip()]
        try:
            need_hours = float(request.query_params.get("need_hours", 0) or 0)
        except ValueError:
            need_hours = 0
        return await self.match(skills=skills, need_hours=need_hours)

    # ── Web routes — create / update / archive ─────────────────────────

    @web_route("POST", "/api/people")
    async def api_create_person(self, request):
        data = await request.json()
        name = (data.get("name") or "").strip()
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
        self.vault_update(n["path"], updates)
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

    # ── Web routes — interactions (quick log) ──────────────────────────

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

    @web_route("POST", "/api/people/{id}/log")
    async def api_log_interaction(self, request):
        ident = request.path_params.get("id", "")
        data = await request.json()
        return await self.log_interaction(ident, data.get("text", ""), data.get("source", ""))

    @web_route("GET", "/api/people/{id}/profile")
    async def api_profile(self, request):
        ident = request.path_params.get("id", "")
        target = self._find_file(ident)
        if not target or not target.exists():
            return {"error": "Person not found"}
        content = await self.read(str(target))
        fm = self._parse_frontmatter(content)
        quick_log = self._parse_quick_log(content)
        note = self._find_note(ident) or {}
        shaped = self._enrich(
            self._person_with_load(
                self._shape_person(
                    note if note else {"name": target.stem, "properties": fm, "path": str(target)}
                )
            )
        )
        # Backlinks
        backlinks: list[str] = []
        vault = self.kernel.config.notes_path or Path(".")
        try:
            results = await self.search(f"[[{target.stem}]]", path=str(vault))
            for r in results[:10]:
                rpath = r if isinstance(r, str) else r.get("path", "")
                if rpath and str(target) not in rpath:
                    try:
                        backlinks.append(str(Path(rpath).relative_to(vault)))
                    except Exception:
                        backlinks.append(rpath)
        except Exception:
            pass
        # Body sections
        body = content
        if body.startswith("---"):
            end = body.find("---", 3)
            if end > 0:
                body = body[end + 3 :].strip()
        sections: dict[str, list[str]] = {}
        current = "intro"
        sections[current] = []
        for line in body.split("\n"):
            if line.startswith("## "):
                current = line[3:].strip()
                sections[current] = []
            else:
                sections.setdefault(current, []).append(line)
        sections_clean = {
            k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()
        }
        return {
            **shaped,
            "frontmatter": fm,
            "quick_log": quick_log,
            "backlinks": backlinks[:30],
            "sections": sections_clean,
            "log_count": len(quick_log),
            "backlink_count": len(backlinks),
        }

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

    # ── Web routes — relationship views ────────────────────────────────

    @web_route("GET", "/api/frequency")
    async def api_frequency(self, request):
        people = await self.list_people()
        groups: dict[str, list] = {
            "weekly": [],
            "monthly": [],
            "quarterly": [],
            "yearly": [],
            "unset": [],
        }
        for p in people:
            freq = (p.get("contact_frequency", "") or "").lower().strip()
            (groups[freq] if freq in groups else groups["unset"]).append(p)
        return groups

    @web_route("GET", "/api/due")
    async def api_due(self, request):
        return self._get_overdue(await self.list_people())

    async def birthdays(self, days: int = 30) -> list[dict]:
        """Upcoming birthdays within `days`. Public — callable via call_app."""
        people = await self.list_people()
        today = date.today()
        out = []
        for p in people:
            bday = p.get("birthday", "")
            if not bday:
                continue
            try:
                bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
                this_year = bday_date.replace(year=today.year)
                if this_year < today:
                    this_year = this_year.replace(year=today.year + 1)
                days_until = (this_year - today).days
                if days_until <= days:
                    out.append(
                        {
                            "id": p["id"],
                            "name": p["name"],
                            "birthday": bday,
                            "days_until": days_until,
                            "turning": this_year.year - bday_date.year
                            if bday_date.year < today.year
                            else 0,
                        }
                    )
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x["days_until"])
        return out

    @web_route("GET", "/api/birthdays")
    async def api_birthdays(self, request):
        days_ahead = int(request.query_params.get("days", 30))
        out = await self.birthdays(days=days_ahead)
        return out

    @web_route("GET", "/api/notifications")
    async def api_notifications(self, request):
        days_ahead = int(self.setting("people.birthday_alert_days", 7))
        people = await self.list_people()
        notifications = []
        today = date.today()
        for p in people:
            bday = p.get("birthday", "")
            if not bday:
                continue
            try:
                bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
                this_year = bday_date.replace(year=today.year)
                if this_year < today:
                    this_year = this_year.replace(year=today.year + 1)
                days_until = (this_year - today).days
                if days_until <= days_ahead:
                    age = today.year - bday_date.year
                    notifications.append(
                        {
                            "type": "birthday",
                            "id": p["id"],
                            "name": p["name"],
                            "message": f"Birthday in {days_until}d"
                            + (f" (turning {age})" if days_until > 0 else " (TODAY!)"),
                            "days": days_until,
                            "priority": "high" if days_until <= 1 else "medium",
                        }
                    )
            except ValueError:
                continue
        if self.setting("people.overdue_alert", True):
            overdue = self._get_overdue(people)
            for c in overdue[:10]:
                notifications.append(
                    {
                        "type": "overdue",
                        "id": c["id"],
                        "name": c["name"],
                        "message": f"Overdue by {c['days_overdue']}d ({c['contact_frequency']})",
                        "days": c["days_overdue"],
                        "priority": "high" if c["overdue_ratio"] >= 2.0 else "medium",
                    }
                )
        notifications.sort(key=lambda n: n["days"])
        return notifications

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

    # ── Web routes — AI over relationships ─────────────────────────────

    @web_route("POST", "/api/ai-suggest")
    async def api_ai_suggest(self, request):
        people = await self.list_people()
        overdue = self._get_overdue(people)
        summary_lines = [
            f"- {p['name']}: {p.get('relationship', '')}, energy={p.get('energy', '')}, "
            f"trust={p.get('trust_level', '')}, last={p.get('last_contact', '')}, "
            f"freq={p.get('contact_frequency', '')}, health={p['health_score']}"
            for p in people[:30]
        ]
        overdue_lines = [f"- {p['name']}: {p['days_overdue']}d overdue" for p in overdue[:10]]
        user_msg = (
            "Contacts:\n" + "\n".join(summary_lines) + "\n\n"
            "Overdue:\n"
            + ("\n".join(overdue_lines) if overdue_lines else "None")
            + "\n\n"
            + AI_SUGGEST_FORMAT
        )
        try:
            result = await self.think(
                user_msg,
                system=AI_SUGGEST_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            items = parse_llm_json(result, fallback=[])
            if not isinstance(items, list):
                items = []
            by_name = {p["name"].lower(): p for p in people}
            for item in items:
                match = by_name.get((item.get("name") or "").lower(), {})
                item["id"] = match.get("id", "")
                item["health_score"] = match.get("health_score", 0)
                item["days_since"] = match.get("days_since")
                item["relationship"] = match.get("relationship", "")
            return {"suggestions": items}
        except Exception as e:
            return {"suggestions": [], "error": str(e)}

    @web_route("POST", "/api/people/{id}/chat")
    async def api_chat(self, request):
        ident = request.path_params.get("id", "")
        data = await request.json()
        question = (data.get("question") or "").strip()
        if not question:
            return {"error": "question required"}
        target = self._find_file(ident)
        if not target:
            return {"error": "Person not found"}
        content = await self.read(str(target))
        fm = self._parse_frontmatter(content)
        quick_log = self._parse_quick_log(content)
        log_text = "\n".join(f"  {e['date']}: {e['text']}" for e in quick_log) or "(no log entries)"
        fm_text = "\n".join(f"  {k}: {v}" for k, v in fm.items()) or "(no frontmatter)"
        body = content
        if body.startswith("---"):
            end = body.find("---", 3)
            if end > 0:
                body = body[end + 3 :].strip()
        display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
        user_msg = (
            f"Person: {display_name}\n\n"
            f"## Contact Info\n{fm_text}\n\n## Quick Log\n{log_text}\n\n"
            f"## Full Note Content\n{body[:3000]}\n\n## Question\n{question}\n\n"
            f"Answer concisely in 2-4 sentences."
        )
        try:
            result = await self.think(
                user_msg,
                system=CHAT_SYSTEM,
                domain="text",
                temperature=0.3,
            )
            return {"id": ident, "name": display_name, "question": question, "answer": result}
        except Exception as e:
            return {"error": f"AI unavailable: {e}"}

    @web_route("GET", "/api/people/{id}/persona")
    async def api_persona(self, request):
        ident = request.path_params.get("id", "")
        target = self._find_file(ident)
        if not target:
            return {"error": "Person not found"}
        content = await self.read(str(target))
        quick_log = self._parse_quick_log(content)
        log_text = "\n".join(f"  {e['date']}: {e['text']}" for e in quick_log) or "(no log entries)"
        body = content
        if body.startswith("---"):
            end = body.find("---", 3)
            if end > 0:
                body = body[end + 3 :].strip()
        display_name = target.stem.lstrip(_FILENAME_PREFIX).replace("-", " ")
        user_msg = (
            f"Interactions with {display_name}:\n{log_text}\n\nAdditional notes:\n{body[:2000]}"
        )
        try:
            result = await self.think(
                user_msg,
                system=PERSONA_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            return {"id": ident, "name": display_name, "persona": result}
        except Exception as e:
            return {"error": f"AI unavailable: {e}"}

    # ── Simulate / personality mixin (absorbed from contacts) ──────────

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
    api_values = _sim.api_values

    # ── CLI ────────────────────────────────────────────────────────────

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

    # ── Hub contributions ──────────────────────────────────────────────

    async def panel_overloaded(self) -> list[dict] | None:
        roster = await self.list_people(active_only=True)
        overloaded = [p for p in roster if p["band"] == "overloaded"]
        if not overloaded:
            return None
        return [
            {
                "label": f"{p['name']} · {int(p['load_ratio'] * 100)}%",
                "href": f"/people/#{p['id']}",
                "icon": "🔴",
            }
            for p in overloaded[:6]
        ]

    async def panel_birthdays(self) -> list[dict] | None:
        """Upcoming birthdays in the next 30 days."""
        people = await self.list_people()
        today = date.today()
        out = []
        for p in people:
            bday = p.get("birthday", "")
            if not bday:
                continue
            try:
                bday_date = datetime.strptime(bday, "%Y-%m-%d").date()
                this_year = bday_date.replace(year=today.year)
                if this_year < today:
                    this_year = this_year.replace(year=today.year + 1)
                days = (this_year - today).days
                if days <= 30:
                    age = this_year.year - bday_date.year
                    out.append(
                        {
                            "title": f"🎂 {p['name']}",
                            "href": f"/people/#{p['id']}",
                            "subtitle": (
                                "TODAY!"
                                if days == 0
                                else f"in {days}d" + (f" · turns {age}" if age > 0 else "")
                            ),
                            "days": days,
                        }
                    )
            except (ValueError, TypeError):
                continue
        if not out:
            return None
        out.sort(key=lambda x: x["days"])
        return out[:5]
