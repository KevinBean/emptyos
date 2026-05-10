"""Scroll — vertical AI-generated short-clip feed.

Personas live as Rooms agents (tier="persona"); scroll proxies the persona
list for its UI. Clips are vault notes tagged `scroll-clip` under
`30_Resources/EmptyOS/scroll/feed/<YYYY-MM-DD>/`. Generation calls Rooms'
chat machinery against the persona-agent — the persona's system_prompt
is the voice, scroll only writes the director instruction.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

from . import director
from .relationships import MemoryStore, RelationshipStore

log = logging.getLogger("emptyos.scroll")


PERSONA_ID_PREFIX = "scroll-"
PERSONA_TIER = "persona"


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "untitled"


def _persona_id(name: str) -> str:
    base = _slug(name)
    return base if base.startswith(PERSONA_ID_PREFIX) else PERSONA_ID_PREFIX + base


def _agent_to_persona(agent: dict) -> dict:
    """Reshape a Rooms agent dict into the persona shape scroll's UI expects."""
    p = agent.get("persona") or {}
    return {
        "id": agent.get("id"),
        "name": agent.get("name", agent.get("id", "")),
        "voice": p.get("voice", ""),
        "draw_style": p.get("draw_style", ""),
        "topics": p.get("topics", []),
        "cadence": p.get("cadence", "weekly"),
        "mood": p.get("mood", "neutral"),
        "needs": p.get("needs", {}),
        "preferences": p.get("preferences", {}),
        "rituals": p.get("rituals", []),
        "linked_person": p.get("linked_person", ""),
        "system_prompt": agent.get("system_prompt", ""),
    }


class ScrollApp(BaseApp):
    async def setup(self):
        await super().setup()
        self.relationships = RelationshipStore(self.data_dir / "relationships.json")
        self.memories = MemoryStore(self.data_dir / "memories")

    async def on_start(self):
        log.info("scroll started")

    # ── Hub panel ───────────────────────────────────────────────

    async def panel_today(self) -> dict | None:
        published = await self.list_clips(status="published")
        drafts = await self.list_clips(status="draft")
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = sum(1 for c in published if (c.get("created") or "").startswith(today))
        if not published and not drafts:
            return None
        value = today_count if today_count else len(drafts)
        label = "today" if today_count else "drafts waiting"
        return {"label": f"Scroll · {label}", "value": str(value)}

    # ── Personas (proxied through Rooms) ─────────────────────────

    @web_route("GET", "/api/personas")
    async def api_personas(self, request):
        return await self.list_personas()

    @web_route("POST", "/api/personas")
    async def api_persona_add(self, request):
        body = await request.json()
        p = await self.add_persona(
            name=body.get("name", ""),
            voice=body.get("voice", ""),
            draw_style=body.get("draw_style", ""),
            topics=body.get("topics", []),
            cadence=body.get("cadence", "weekly"),
            system_prompt=body.get("system_prompt", ""),
        )
        if "error" in p:
            return p
        await self.emit("scroll:persona_added", {"id": p["id"]})
        return p

    @web_route("GET", "/api/personas/{pid}")
    async def api_persona_get(self, request):
        p = await self.get_persona(request.path_params["pid"])
        return p or {"error": "Persona not found"}

    async def list_personas(self) -> list[dict]:
        try:
            agents = await self.call_app("rooms", "list_agents", tier=PERSONA_TIER)
        except Exception:
            return []
        return [_agent_to_persona(a) for a in agents or []]

    async def get_persona(self, pid: str) -> dict | None:
        try:
            agent = await self.call_app("rooms", "get_agent", agent_id=pid)
        except Exception:
            return None
        if not agent or agent.get("tier") != PERSONA_TIER:
            return None
        return _agent_to_persona(agent)

    async def add_persona(
        self,
        name: str,
        voice: str = "",
        draw_style: str = "",
        topics: list | None = None,
        cadence: str = "weekly",
        system_prompt: str = "",
    ) -> dict:
        if not name:
            return {"error": "name required"}
        pid = _persona_id(name) or PERSONA_ID_PREFIX + uuid.uuid4().hex[:8]
        try:
            existing = await self.call_app("rooms", "has_agent", agent_id=pid)
        except Exception:
            return {"error": "rooms app unavailable"}
        if existing:
            return {"error": f"Persona '{pid}' already exists"}
        agent = {
            "id": pid,
            "name": name,
            "tier": PERSONA_TIER,
            "system_prompt": system_prompt or f"You are {name}.",
            "knowledge_files": [],
            "knowledge_char_limit": 4000,
            "model": "",
            "temperature": 0.7,
            "tools": [],
            "server_actions": {},
            "builtin": False,
            "persona": {
                "voice": voice,
                "draw_style": draw_style,
                "topics": list(topics or []),
                "cadence": cadence,
                "mood": "neutral",
                "needs": {"social": 0.5, "fun": 0.5, "energy": 0.7},
                "preferences": {},
                "rituals": [],
            },
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        await self.call_app("rooms", "save_agent", agent=agent)
        return _agent_to_persona(agent)

    async def set_mood(self, pid: str, mood: str) -> dict:
        try:
            agent = await self.call_app("rooms", "get_agent", agent_id=pid)
        except Exception:
            return {"error": "rooms unavailable"}
        if not agent or agent.get("tier") != PERSONA_TIER:
            return {"error": "Persona not found"}
        agent.setdefault("persona", {})["mood"] = mood
        await self.call_app("rooms", "save_agent", agent=agent)
        await self.emit("scroll:mood_changed", {"id": pid, "mood": mood})
        return {"ok": True, "mood": mood}

    # ── Feed (clips stay in vault) ──────────────────────────────

    @web_route("GET", "/api/feed")
    async def api_feed(self, request):
        return await self.list_clips(status="published")

    @web_route("GET", "/api/drafts")
    async def api_drafts(self, request):
        return await self.list_clips(status="draft")

    @web_route("GET", "/api/feed/next")
    async def api_next_clip(self, request):
        cursor = request.query_params.get("after", "")
        return await self.next_clip(after=cursor)

    @web_route("GET", "/api/clips/{cid}")
    async def api_clip_get(self, request):
        cid = request.path_params["cid"]
        clip = await self._find_clip(cid)
        if not clip:
            return {"error": "Clip not found"}
        body = ""
        path = clip.get("_path")
        if path:
            try:
                body = self.vault_read_body(path) or ""
            except Exception:
                body = ""
        return {**clip, "body": body}

    @web_route("POST", "/api/clips/{cid}/publish")
    async def api_clip_publish(self, request):
        cid = request.path_params["cid"]
        clip = await self._find_clip(cid)
        out = await self.set_clip_status(cid, "published")
        if out.get("ok") and clip:
            self.memories.add_for_each(clip.get("participants") or [], {
                "kind": "clip_published",
                "clip_id": cid,
                "summary": clip.get("title", ""),
            })
            await self.emit("scroll:clip_published", {
                "id": cid,
                "title": clip.get("title"),
                "participants": clip.get("participants"),
                "shape": clip.get("shape"),
            })
        return out

    @web_route("POST", "/api/clips/{cid}/like")
    async def api_clip_like(self, request):
        cid = request.path_params["cid"]
        clip = await self._find_clip(cid)
        if clip:
            self.memories.add_for_each(clip.get("participants") or [], {
                "kind": "clip_liked", "clip_id": cid,
                "summary": f"liked: {clip.get('title','')}",
            })
        await self.emit("scroll:clip_liked", {"id": cid, "participants": (clip or {}).get("participants")})
        return {"ok": True}

    @web_route("POST", "/api/clips/{cid}/skip")
    async def api_clip_skip(self, request):
        cid = request.path_params["cid"]
        clip = await self._find_clip(cid)
        await self.emit("scroll:clip_skipped", {"id": cid, "participants": (clip or {}).get("participants")})
        return {"ok": True}

    async def _find_clip(self, cid: str) -> dict | None:
        for c in await self.list_clips(status="draft") + await self.list_clips(status="published"):
            if c["id"] == cid:
                return c
        return None

    # ── Relationships + memories ────────────────────────────────

    @web_route("GET", "/api/relationships")
    async def api_relationships(self, request):
        return self.relationships.all()

    @web_route("GET", "/api/relationships/{a}/{b}")
    async def api_relationship_get(self, request):
        a = request.path_params["a"]
        b = request.path_params["b"]
        return self.relationships.get(a, b)

    @web_route("POST", "/api/relationships/{a}/{b}")
    async def api_relationship_update(self, request):
        a = request.path_params["a"]
        b = request.path_params["b"]
        deltas = await request.json()
        return self.relationships.update(a, b, deltas)

    @web_route("GET", "/api/memories/{pid}")
    async def api_memories(self, request):
        pid = request.path_params["pid"]
        k = int(request.query_params.get("k", "10"))
        return {"persona": pid, "events": self.memories.recent(pid, k=k)}

    @web_route("POST", "/api/generate")
    async def api_generate(self, request):
        body = await request.json()
        return await self.generate_clip(
            persona_id=body.get("persona_id", ""),
            shape=body.get("shape", "monologue"),
            other_id=body.get("other_id", ""),
            topic_hint=body.get("topic_hint", ""),
        )

    # ── CLI ─────────────────────────────────────────────────────

    @cli_command("personas")
    async def cli_personas(self):
        for p in await self.list_personas():
            print(f"{p.get('id'):24s}  {p.get('name')}  ({p.get('cadence')}, {p.get('mood')})")

    @cli_command("feed")
    async def cli_feed(self):
        for c in await self.list_clips(status="published"):
            print(f"{c.get('id'):24s}  {c.get('title')}  [{c.get('persona')}]")

    # ── Clip storage (vault) ────────────────────────────────────

    async def list_clips(self, status: str = "published") -> list[dict]:
        notes = self.vault_query(tags=["scroll-clip"], status=status)
        out = []
        for n in notes:
            fm = n.get("properties", {}) or {}
            audio = fm.get("audio") or ""
            image = fm.get("image") or ""
            music = fm.get("music") or ""
            comfyui = self.service("comfyui") if image else None
            image_url = ""
            if image and comfyui:
                try:
                    image_url = await comfyui.get_image_url(image)
                except Exception:
                    image_url = ""
            out.append({
                "id": fm.get("id"),
                "title": fm.get("title", ""),
                "persona": fm.get("persona", ""),
                "participants": fm.get("participants", []),
                "shape": fm.get("shape", "monologue"),
                "status": fm.get("status", "draft"),
                "created": fm.get("created", ""),
                "audio": audio,
                "audio_url": f"/scroll/api/audio/{audio}" if audio else "",
                "image": image,
                "image_url": image_url,
                "music": music,
                "music_url": f"/music-library/api/stream/{music}" if music else "",
                "_path": n.get("path"),
            })
        out.sort(key=lambda c: c.get("created", ""), reverse=True)
        return out

    async def next_clip(self, after: str = "") -> dict | None:
        clips = await self.list_clips(status="published")
        if not clips:
            return None
        if not after:
            return clips[0]
        for i, c in enumerate(clips):
            if c["id"] == after and i + 1 < len(clips):
                return clips[i + 1]
        return None

    async def set_clip_status(self, cid: str, status: str) -> dict:
        for c in await self.list_clips(status="draft") + await self.list_clips(status="published"):
            if c["id"] == cid:
                self.vault_update(c["_path"], {"status": status})
                return {"ok": True}
        return {"error": "Clip not found"}

    # ── Clip generation ─────────────────────────────────────────

    async def generate_clip(
        self,
        persona_id: str,
        shape: str = "monologue",
        other_id: str = "",
        topic_hint: str = "",
    ) -> dict:
        if shape not in ("monologue", "dialogue", "news-flash"):
            return {"error": f"unknown shape '{shape}'"}
        a = await self.get_persona(persona_id)
        if not a:
            return {"error": f"Persona '{persona_id}' not found"}

        if shape == "monologue":
            return await self._generate_monologue(a, topic_hint)
        b = await self.get_persona(other_id) if other_id else None
        if not b:
            return {"error": f"shape={shape} requires other_id (not '{other_id}')"}
        if shape == "dialogue":
            return await self._generate_dialogue(a, b, topic_hint)
        return await self._generate_news_flash(a, b)

    async def _rooms_chat(self, agent_id: str, text: str) -> str:
        try:
            result = await self.call_app("rooms", "chat", agent_id=agent_id, text=text)
        except Exception as e:
            log.warning("scroll: rooms chat failed (%s)", e)
            return ""
        return (result or {}).get("response", "").strip()

    async def _generate_monologue(self, persona: dict, topic_hint: str) -> dict:
        prompt = director.MONOLOGUE_DIRECTOR.format(
            topic_hint_block=director.topic_hint_block(topic_hint),
        )
        script = await self._rooms_chat(persona["id"], prompt)
        clip = self._save_draft_clip(
            shape="monologue",
            persona_id=persona["id"],
            participants=[persona["id"]],
            script=script,
            title=self._derive_title(script),
        )
        await self._enrich_clip(clip, persona)
        return clip

    async def _generate_dialogue(self, a: dict, b: dict, topic_hint: str) -> dict:
        rel = self.relationships.get(a["id"], b["id"])
        rel_summary = director.fmt_relationship(rel)
        line_a = await self._rooms_chat(
            a["id"],
            director.DIALOGUE_DIRECTOR_OPEN.format(
                other_name=b["name"],
                relationship_summary=rel_summary,
                topic_hint_block=director.topic_hint_block(topic_hint),
            ),
        )
        line_b = await self._rooms_chat(
            b["id"],
            director.DIALOGUE_DIRECTOR_REPLY.format(
                other_name=a["name"],
                previous_line=line_a,
                relationship_summary=rel_summary,
            ),
        )
        line_a2 = await self._rooms_chat(
            a["id"],
            director.DIALOGUE_DIRECTOR_REPLY.format(
                other_name=b["name"],
                previous_line=line_b,
                relationship_summary=rel_summary,
            ),
        )
        script = (
            f"{a['name']}: {line_a}\n\n"
            f"{b['name']}: {line_b}\n\n"
            f"{a['name']}: {line_a2}"
        )
        clip = self._save_draft_clip(
            shape="dialogue",
            persona_id=a["id"],
            participants=[a["id"], b["id"]],
            script=script,
            title=f"{a['name']} & {b['name']}",
        )
        # Use the first speaker as the visual anchor; TTS reads the whole script
        # in a single voice for now (multi-voice playback is a Phase 8 concern).
        await self._enrich_clip(clip, a)
        return clip

    async def _generate_news_flash(self, a: dict, b: dict) -> dict:
        anchor_id = await self._ensure_news_anchor()
        rel = self.relationships.get(a["id"], b["id"])
        rel_summary = director.fmt_relationship(rel)
        recent = self.memories.recent(a["id"], k=1) + self.memories.recent(b["id"], k=1)
        recent.sort(key=lambda e: e.get("ts", 0), reverse=True)
        recent_summary = (recent[0].get("summary") if recent else None) or "(no recent event recorded)"
        prompt = director.NEWS_FLASH_DIRECTOR.format(
            a_name=a["name"],
            b_name=b["name"],
            relationship_summary=rel_summary,
            recent_summary=recent_summary,
        )
        script = await self._rooms_chat(anchor_id, prompt)
        clip = self._save_draft_clip(
            shape="news-flash",
            persona_id=anchor_id,
            participants=[a["id"], b["id"]],
            script=script,
            title=self._derive_title(script),
        )
        # Image anchors on persona A's draw_style; voice uses the news anchor's default
        await self._enrich_clip(clip, a)
        return clip

    async def _ensure_news_anchor(self) -> str:
        anchor_id = "scroll-news-anchor"
        if await self.call_app("rooms", "has_agent", agent_id=anchor_id):
            return anchor_id
        await self.call_app("rooms", "save_agent", agent={
            "id": anchor_id,
            "name": "Island News",
            "tier": "scroll-system",
            "system_prompt": director.NEWS_ANCHOR_SYSTEM,
            "knowledge_files": [],
            "knowledge_char_limit": 1000,
            "model": "",
            "temperature": 0.6,
            "tools": [],
            "server_actions": {},
            "builtin": True,
            "persona": {"voice": "wry observational narrator", "topics": []},
            "created": datetime.now().isoformat(timespec="seconds"),
        })
        return anchor_id

    # ── Enrichment: TTS + image + bg music (best-effort) ───────

    async def _enrich_clip(self, clip: dict, persona: dict) -> None:
        """Layer media onto a draft clip. Each step is best-effort —
        a missing capability never blocks the draft from existing."""
        updates: dict = {}
        script = clip.get("script") or ""

        # 1. TTS — narrate the script
        try:
            audio = await self.speak(script)
            audio_name = Path(str(audio)).name if audio else ""
            if audio_name:
                updates["audio"] = audio_name
                clip["audio_url"] = f"/scroll/api/audio/{audio_name}"
        except Exception as e:
            log.info("scroll: TTS skipped (%s)", e)

        # 2. Image — cover for the clip
        try:
            img_prompt = self._build_image_prompt(persona, clip)
            filename = await self.draw(img_prompt)
            if filename:
                updates["image"] = filename
                comfyui = self.service("comfyui")
                if comfyui:
                    try:
                        clip["image_url"] = await comfyui.get_image_url(filename)
                    except Exception:
                        pass
        except Exception as e:
            log.info("scroll: image skipped (%s)", e)

        # 3. Background music — random pick from music-library
        try:
            music = self.kernel.apps.instances.get("music-library")
            if music and hasattr(music, "_library"):
                pool = await music._library.list()
                if pool:
                    pick = random.choice(pool)
                    fname = pick.get("file") or pick.get("filename")
                    if fname:
                        updates["music"] = fname
                        # Audio file path served via music-library's audio endpoint
                        audio_files = await music._library.audio_files(fname)
                        if audio_files:
                            first = audio_files[0] if isinstance(audio_files, list) else audio_files
                            if isinstance(first, dict):
                                first = first.get("url") or first.get("path") or ""
                            if first:
                                clip["music_url"] = first if str(first).startswith("/") else f"/music-library/api/stream/{first}"
        except Exception as e:
            log.info("scroll: bg music skipped (%s)", e)

        if updates:
            self._merge_clip_meta(clip, updates)

    def _build_image_prompt(self, persona: dict, clip: dict) -> str:
        style = (persona.get("draw_style") or "").strip()
        title = (clip.get("title") or "").strip()
        first = (clip.get("script") or "").split("\n", 1)[0][:120]
        parts = [
            "vertical 9:16 cinematic still",
            title,
            first,
            f"in the style of: {style}" if style else "",
        ]
        return ". ".join(p for p in parts if p)[:500]

    def _merge_clip_meta(self, clip: dict, updates: dict) -> None:
        """Persist updates to the clip's vault note frontmatter and the in-memory dict."""
        path = clip.get("_path")
        if path:
            try:
                self.vault_update(path, updates)
            except Exception as e:
                log.warning("scroll: vault_update failed for %s: %s", path, e)
        clip.update(updates)

    @web_route("GET", "/api/audio/{filename}")
    async def api_audio(self, request):
        from emptyos.capabilities.audio import AUDIO_DIR, AUDIO_MIME
        from starlette.responses import FileResponse, JSONResponse

        filename = request.path_params["filename"]
        if "/" in filename or "\\" in filename or ".." in filename:
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        path = (AUDIO_DIR / filename).resolve()
        if not str(path).startswith(str(AUDIO_DIR.resolve())):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        mime = AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=mime)

    @web_route("GET", "/api/seed-topic")
    async def api_seed_topic(self, request):
        """Return a single topic-hint suggestion drawn from the vault.

        Picks a random recent note title from a configured tag pool, or
        falls back to a generic search across journal/notes/captures.
        """
        try:
            pool: list[dict] = []
            for tag in ("daily", "song", "kb", "note"):
                try:
                    pool.extend(self.vault_query(tags=[tag]) or [])
                except Exception:
                    continue
            if not pool:
                return {"hint": ""}
            pick = random.choice(pool)
            title = (pick.get("name") or "").replace(".md", "").strip()
            return {"hint": title, "source": pick.get("path")}
        except Exception as e:
            return {"hint": "", "error": str(e)}

    @staticmethod
    def _derive_title(script: str) -> str:
        first = re.split(r"[.!?\n]", (script or "").strip(), maxsplit=1)[0]
        first = first.strip()
        return first[:60] + ("…" if len(first) > 60 else "")

    def _save_draft_clip(
        self,
        *,
        shape: str,
        persona_id: str,
        participants: list[str],
        script: str,
        title: str,
    ) -> dict:
        cid = f"clip-{uuid.uuid4().hex[:10]}"
        today = datetime.now().strftime("%Y-%m-%d")
        feed_base = self.vault_config("feed") or "30_Resources/EmptyOS/scroll/feed"
        path = f"{feed_base}/{today}/{cid}.md"
        fm = {
            "id": cid,
            "title": title or "(untitled)",
            "persona": persona_id,
            "participants": participants,
            "shape": shape,
            "status": "draft" if self.app_config("draft_review", True) else "published",
            "created": datetime.now().isoformat(timespec="seconds"),
            "tags": ["scroll-clip"],
        }
        body = script or "(no script generated)"
        self.vault_create_note(path, fm, body)
        self.memories.add_for_each(participants, {
            "kind": "clip_drafted",
            "clip_id": cid,
            "shape": shape,
            "summary": title or shape,
            "participants": participants,
        })
        return {
            "id": cid,
            "title": fm["title"],
            "persona": persona_id,
            "participants": participants,
            "shape": shape,
            "status": fm["status"],
            "created": fm["created"],
            "_path": path,
            "script": body,
        }
