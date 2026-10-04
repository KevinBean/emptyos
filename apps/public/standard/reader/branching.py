"""Reader branching mode — interactive fiction over the decision-graph engine.

Extends reader so a "book" can be a *branching story*: weave a CEFR-graded graph
from a premise (one ``think`` call, with the full beat prose baked into every
node), then walk it turn-by-turn (:class:`DecisionRun`) with per-beat scene image
+ narration reusing reader's existing ``draw``/TTS plumbing. Aimed at English
learning — stories are graded to a CEFR band and the reader's tap-to-define +
read-aloud affordances come for free.

Reuses ``emptyos/sdk/decision_graph.py`` — reader is that primitive's **consumer
#2** (the SDK-blessing consumer after branching tours). It is also the first
turn-by-turn *server-driven* walk (POST a choice → get the next View), the
graduation flagged in ``.claude/rules/tour-steps.md``.

PLAY MAKES ZERO PER-TURN LLM CALLS — prose is generated once at weave time, so
walking the graph is pure data. Only :meth:`weave_story` hits ``think``.

Dark-flagged: ``[apps.reader] feature.story-mode.enabled`` (default off, read via
``app_config``). When off, story routes return ``{disabled: true}`` and the
library lists no stories, so reader is behaviourally unchanged.

────────────────────────────────────────────────────────────────────────────
Bound to ReaderApp as a mixin (matches ScenesMixin / ProductionMixin). Reaches
into ScenesMixin (``_world_card_text`` / ``_present_character_anchors`` /
``_scenes_dir`` / ``_download_comfyui_image`` / ``self.draw``) and BaseApp
(``_speak_cached`` / ``vault_create_note`` / ``vault_root`` / ``think`` /
``app_config`` / ``state_data`` / ``save_state``).
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from emptyos.sdk import parse_frontmatter, parse_llm_json, strip_frontmatter, web_route
from emptyos.sdk.decision_graph import (
    DecisionGraph,
    DecisionGraphError,
    DecisionRun,
    validate_graph,
)

from ._helpers import _slugify
from .scenes import SCENE_SYSTEM

log = logging.getLogger("emptyos.reader.branching")

STORY_TAG = "story"
INTERACTIVE_SUBDIR = "interactive"
WEAVE_MAX_TRIES = 3
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


WEAVE_SYSTEM = """You are a master of interactive fiction who writes BRANCHING
stories for language learners. You output ONE JSON object describing a decision
graph: a story the reader walks by making choices. Each node is a "beat" — a
short passage of prose — and choices fork the path.

OUTPUT SHAPE (JSON only, no prose around it, no markdown fences):
{
  "start": "<id of the opening node>",
  "nodes": [
    {
      "id": "snake_case_id",
      "kind": "content",          // or "end" for a terminal beat
      "data": {
        "title": "Short beat title (3-6 words)",
        "text": "The FULL prose of this beat, 60-140 words, written AT the target
                 CEFR level. Second person ('you'). Vivid but level-appropriate."
      },
      "transitions": [
        {"to": "<another node id>", "kind": "choice",
         "label": "The choice the reader sees (a first-person action, <=12 words)"}
      ]
    }
  ],
  "vars": {},
  "meta": {
    "title": "Story title",
    "bible": {
      "setting": "25-50 words, concrete physical description for image generation",
      "era": "time/aesthetic descriptor",
      "characters": [
        {"name": "Name",
         "anchor": "12-25 word LOCKED visual phrase (build, age, hair, clothing
                    colours) — reused verbatim so illustrations stay consistent"}
      ],
      "style_hint": "one short visual-mood phrase"
    }
  }
}

HARD STRUCTURE RULES (a graph that breaks these is rejected and you will be re-asked):
- Exactly one "start" id, and it must be one of the nodes.
- Every transition "to" must point at a real node id. No dangling targets.
- At least one node has "kind": "end" and it must be REACHABLE from start.
- No orphan nodes (every node reachable from start by following transitions).
- A non-"end" node MUST have at least one "choice" transition. No dead ends
  that aren't endings.
- 8-16 nodes. 2-3 choices at most branch points. Reach an ending in 4-7 beats.
- Give the story REAL branching (choices that lead to genuinely different beats),
  not a single line dressed up with fake choices.

LANGUAGE GRADING (the point of this feature):
- Write ALL beat prose AND choice labels at the requested CEFR level. Respect the
  vocabulary and grammar band of that level — do not exceed it.
- If target words are provided, weave them in NATURALLY across beats. Do not
  define them; let context carry the meaning. Never force all of them into one beat.

DO NOT:
- output anything outside the single JSON object (no preamble, no fences).
- write beats longer than ~140 words or shorter than ~50.
- create choices that all lead to the same node.
- use a second language unless the target language itself is non-English.
- put markdown headings inside beat "text".
"""


SUGGEST_PREMISE_SYSTEM = """You propose premises for BRANCHING interactive
fiction, inspired by the moods/subjects of a reader's library (you receive only
book TITLES — no contents). Each premise is ONE vivid sentence, second-person
playable ("You …"), with obvious room to branch (a choice, a mystery, a door).

Return JSON only: {"premises": ["...", "...", ...]}. No prose, no fences.

Do NOT:
- reuse a book's actual plot or characters — riff on its mood/theme/setting only
- write more than one sentence per premise
- exceed the count requested
- name the source books"""


def _coerce_target_words(value) -> list[str]:
    """Normalize persisted learning words from graph meta or frontmatter."""
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value:
        word = str(raw or "").strip()
        if word and word not in out:
            out.append(word)
    return out[:20]


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _story_learning_meta(graph: "DecisionGraph", frontmatter: dict) -> dict:
    meta = graph.meta or {}
    words = _coerce_target_words(
        meta.get("target_words") or frontmatter.get("target_words") or []
    )
    checks = _as_bool(
        meta.get("comprehension_checks", frontmatter.get("comprehension_checks", False))
    )
    return {
        "lang": frontmatter.get("lang") or "English",
        "cefr": frontmatter.get("cefr") or "",
        "target_words": words,
        "comprehension_checks": checks,
    }


def _story_game_brief(
    graph: "DecisionGraph", frontmatter: dict, slug: str
) -> tuple[str, dict]:
    """Build the game brief while preserving the story's learning contract."""
    meta = graph.meta or {}
    bible = meta.get("bible") or {}
    learning = _story_learning_meta(graph, frontmatter)
    chars = ", ".join(
        f"{c.get('name')} ({c.get('anchor')})"
        for c in (bible.get("characters") or [])[:3]
        if c.get("name")
    )
    beats = "; ".join(
        (node.data.get("title") or "").strip()
        for node in list(graph.nodes.values())[:7]
        if (node.data.get("title") or "").strip()
    )
    premise = meta.get("premise") or frontmatter.get("premise") or meta.get("title") or slug
    cefr = learning["cefr"] or "the story's original level"
    lang = learning["lang"]
    words = learning["target_words"]

    learning_rules = [
        "Language-learning contract:",
        f"- Keep every player-facing line (NPC dialogue, signs, item text, choices, and feedback) in {lang} at {cefr} CEFR difficulty.",
        "- Make language use part of play: require 2-3 short dialogue, reading, or meaning-in-context interactions before the goal unlocks.",
        "- Integrate learning into exploration and character interaction; do not interrupt the game with a detached quiz screen.",
        "- Wrong answers must give a brief level-appropriate hint and allow an immediate retry without punishment.",
        "- End with a concise recap of the language the player used to win.",
    ]
    if words:
        learning_rules.append(
            "- Reuse these target words naturally and make their meaning inferable from context: "
            + ", ".join(words)
            + "."
        )
    else:
        learning_rules.append(
            "- Include at least one meaning-in-context vocabulary interaction drawn from the story setting."
        )
    if learning["comprehension_checks"]:
        learning_rules.append(
            "- Preserve the story's comprehension-check mode with at least two checkpoints about events, motives, or word meaning."
        )

    brief = (
        "Top-down adventure game based on this story. "
        f"Premise: {premise}. "
        f"Setting: {bible.get('setting', '')}. "
        f"Mood: {bible.get('style_hint', '')}. "
        + (f"Characters (as the player + NPCs): {chars}. " if chars else "")
        + (f"Evoke these story beats as areas/NPCs/signs: {beats}. " if beats else "")
        + "The player explores tiled areas, talks to NPCs, and reaches a goal that resolves the story. "
        + "Keep it winnable in a couple of minutes.\n\n"
        + "\n".join(learning_rules)
    )
    return brief, learning


class BranchingMixin:
    # ── flag + paths ─────────────────────────────────────────────

    def _story_enabled(self) -> bool:
        return bool(self.app_config("feature.story-mode.enabled", False))

    def _interactive_dir_rel(self) -> str:
        return f"{self._books_dir_rel()}/{INTERACTIVE_SUBDIR}"

    def _story_path_rel(self, slug: str) -> str:
        return f"{self._interactive_dir_rel()}/{slug}.md"

    # ── story library (kept separate from _list_books so linear paths are
    #    untouched — the frontend merges /api/books + /api/stories) ─────────

    def _story_row(self, slug: str, props: dict) -> dict:
        """Build a story-list row from a slug + its frontmatter (shared by the
        VaultIndex query and the filesystem fallback)."""
        return {
            "slug": slug,
            "title": props.get("title") or slug,
            "lang": props.get("lang") or "English",
            "cefr": props.get("cefr") or "",
            "premise": props.get("premise") or "",
            "target_words": _coerce_target_words(props.get("target_words") or []),
            "comprehension_checks": _as_bool(props.get("comprehension_checks", False)),
            "in_progress": slug in (self.state_data.get("playthroughs", {}) or {}),
            "story": True,
        }

    def _list_stories(self) -> list[dict]:
        """List woven stories. Stories are app-typed notes (kind:story, tags:[story])
        with a strict frontmatter contract, so query the VaultIndex. Falls back to a
        folder scan if the index is cold/unavailable so an existing or freshly-woven
        story is never silently hidden (eos-vault-migration hard rule)."""
        from pathlib import Path as _Path
        rel = self._interactive_dir_rel()
        out: list[dict] = []
        for r in self.vault_query(tags=[STORY_TAG], folder=rel):
            props = r.get("properties") or {}
            if (props.get("kind") or "") != STORY_TAG:
                continue
            path = (r.get("path") or "").replace("\\", "/")
            if rel not in path:  # belt-and-braces folder scoping
                continue
            out.append(self._story_row(_slugify(_Path(path).stem), props))
        if out:
            return sorted(out, key=lambda s: (s["title"] or "").lower())
        return self._list_stories_fs()

    def _list_stories_fs(self) -> list[dict]:
        """Index-independent fallback for _list_stories."""
        base = self.vault_root / self._interactive_dir_rel()
        if not base.exists():
            return []
        out = []
        for entry in sorted(base.glob("*.md")):
            if entry.name.startswith(("_", ".")):
                continue
            try:
                raw = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm = parse_frontmatter(raw)
            if (fm.get("kind") or "") != STORY_TAG:
                continue
            out.append(self._story_row(_slugify(entry.stem), fm))
        return out

    # ── graph load / save ────────────────────────────────────────

    def _load_story_graph(self, slug: str) -> tuple["DecisionGraph | None", dict]:
        """Return (graph, frontmatter) for a story slug, or (None, {})."""
        full = self.vault_root / self._story_path_rel(slug)
        if not full.exists():
            return None, {}
        raw = full.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(raw)
        body = strip_frontmatter(raw)
        m = _JSON_BLOCK.search(body)
        if not m:
            return None, fm
        data = parse_llm_json(m.group(1))
        if not isinstance(data, dict) or "nodes" not in data:
            return None, fm
        try:
            return DecisionGraph.from_dict(data), fm
        except (DecisionGraphError, KeyError, TypeError) as e:
            log.warning("story graph parse failed for %s: %s", slug, e)
            return None, fm

    def _save_story(
        self,
        slug: str,
        title: str,
        graph: "DecisionGraph",
        *,
        lang: str,
        cefr: str,
        premise: str,
        target_words: list[str] | None = None,
        with_checks: bool = False,
    ) -> str:
        rel = self._story_path_rel(slug)
        import json as _json

        body = (
            f"# {title}\n\n"
            f"_{premise}_\n\n"
            "Interactive story — graph data below (edit in the Reader, not by hand).\n\n"
            "```json\n" + _json.dumps(graph.to_dict(), ensure_ascii=False, indent=2) + "\n```\n"
        )
        self.vault_create_note(
            rel,
            frontmatter={
                "tags": [STORY_TAG],
                "kind": STORY_TAG,
                "author": "ai",  # woven by the LLM (authorship-boundary rule)
                "title": title,
                "lang": lang,
                "cefr": cefr,
                "premise": premise,
                "target_words": _coerce_target_words(target_words or []),
                "comprehension_checks": bool(with_checks),
                "created": datetime.now(timezone.utc).isoformat(),
            },
            body=body,
        )
        return rel

    # ── validity gate ────────────────────────────────────────────

    @staticmethod
    def _reachable(graph: "DecisionGraph") -> set[str]:
        if graph.start not in graph.nodes:
            return set()
        seen = {graph.start}
        stack = [graph.start]
        while stack:
            cur = graph.nodes[stack.pop()]
            for t in cur.transitions:
                if t.to in graph.nodes and t.to not in seen:
                    seen.add(t.to)
                    stack.append(t.to)
        return seen

    @staticmethod
    def _repair_graph(graph: "DecisionGraph") -> "DecisionGraph":
        """Salvage near-valid model output BEFORE validation. Safe — never
        invents content: (1) drop transitions to undefined nodes (the common
        'forgot to define the target' slip), (2) mark any node left with no
        transitions as an ending. Turns the typical dangling-target/no-end
        failure into a valid graph instead of a wasted retry."""
        ids = set(graph.nodes)
        for node in graph.nodes.values():
            kept = [t for t in node.transitions if t.to in ids]
            if len(kept) != len(node.transitions):
                node.transitions = kept
            if not node.transitions and node.kind != "end":
                node.kind = "end"
        return graph

    def _story_validation_errors(self, graph: "DecisionGraph") -> list[str]:
        """Hard errors only + the story-specific 'reachable ending' requirement."""
        errors = [e for e in validate_graph(graph) if not e.startswith("warn:")]
        reachable = self._reachable(graph)
        if not any(graph.nodes[n].kind == "end" for n in reachable):
            errors.append("no reachable end node")
        # Non-end reachable nodes must offer at least one choice (no silent dead ends)
        for nid in reachable:
            node = graph.nodes[nid]
            if node.kind != "end" and not any(t.kind == "choice" for t in node.transitions):
                errors.append(f"node {nid!r} is a dead end but not marked 'end'")
        return errors

    # ── weave (the only think call) ──────────────────────────────

    async def weave_story(
        self,
        premise: str,
        *,
        lang: str = "English",
        cefr: str = "C1",
        target_words: list[str] | None = None,
        with_checks: bool = False,
    ) -> dict:
        premise = (premise or "").strip()
        if not premise:
            return {"error": "premise is required"}
        tw = [w.strip() for w in (target_words or []) if w and w.strip()]
        user = (
            f"Target language: {lang}\n"
            f"Target CEFR level: {cefr}\n"
            f"Premise: {premise}\n"
        )
        if tw:
            user += f"Target vocabulary to weave in naturally: {', '.join(tw[:20])}\n"
        if with_checks:
            user += (
                "Include occasional comprehension-style choices (a beat ends by asking "
                "what just happened / what a word meant; one choice is right and leads on, "
                "others lead to a brief re-explanation beat that then rejoins).\n"
            )

        last_errors: list[str] = []
        for attempt in range(WEAVE_MAX_TRIES):
            try:
                raw = await self.think(
                    user,
                    system=WEAVE_SYSTEM,
                    domain="text",
                    min_ability="standard",
                    temperature=0.7,
                )
            except Exception as e:
                log.warning("weave think failed (attempt %d): %s", attempt + 1, e)
                last_errors = [f"think failed: {e}"]
                continue
            data = parse_llm_json(raw)
            if not isinstance(data, dict):
                last_errors = ["model did not return a JSON object"]
                continue
            try:
                graph = DecisionGraph.from_dict(data)
            except (DecisionGraphError, KeyError, TypeError) as e:
                last_errors = [f"graph build failed: {e}"]
                continue
            self._repair_graph(graph)
            errors = self._story_validation_errors(graph)
            if not errors:
                try:
                    title = str((graph.meta or {}).get("title") or premise[:48]).strip()
                    slug = self._unique_story_slug(_slugify(title) or "story")
                    graph.meta.setdefault("title", title)
                    graph.meta.setdefault("premise", premise)
                    graph.meta["target_words"] = tw
                    graph.meta["comprehension_checks"] = bool(with_checks)
                    rel = self._save_story(
                        slug,
                        title,
                        graph,
                        lang=lang,
                        cefr=cefr,
                        premise=premise,
                        target_words=tw,
                        with_checks=with_checks,
                    )
                except Exception as e:  # a valid graph that won't persist — surface it, don't 500
                    log.warning("story save failed: %s", e)
                    return {"error": f"woven a valid story but saving failed: {e}"}
                return {
                    "ok": True,
                    "slug": slug,
                    "title": title,
                    "path": rel,
                    "nodes": len(graph.nodes),
                    "lang": lang,
                    "cefr": cefr,
                    "target_words": tw,
                    "comprehension_checks": bool(with_checks),
                }
            last_errors = errors
            # Feed the errors back so the retry can self-correct.
            user += f"\nYour previous attempt was rejected for: {'; '.join(errors[:5])}. Fix and re-output."

        return {"error": "could not weave a valid story", "details": last_errors[:5]}

    async def suggest_premises(self, n: int = 5) -> dict:
        """Propose vault-grounded story premises. Cloud-safe: feeds the model only
        book TITLES (Rule 19 — never note bodies). Grounds on reader's own library."""
        titles = [b["title"] for b in self._list_books() if b.get("title")][:40]
        if not titles:
            return {"premises": [], "note": "no books in the library to draw from"}
        user = (
            "The reader's library (titles only):\n"
            + "\n".join(f"- {t}" for t in titles)
            + f"\n\nPropose {n} interactive-fiction premises that riff on the moods and "
            "subjects suggested by these titles."
        )
        try:
            raw = await self.think(user, system=SUGGEST_PREMISE_SYSTEM, domain="text", temperature=0.9)
        except Exception as e:
            return {"premises": [], "error": f"suggestion failed: {e}"}
        data = parse_llm_json(raw)
        prem = data.get("premises") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        prem = [str(p).strip() for p in (prem or []) if str(p).strip()][:n]
        return {"premises": prem}

    def _unique_story_slug(self, base: str) -> str:
        existing = {s["slug"] for s in self._list_stories()}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    # ── play (server-driven walk, persisted in state_data) ───────

    def _playthroughs(self) -> dict:
        return self.state_data.setdefault("playthroughs", {})

    def _prime_world_card(self, slug: str, graph: "DecisionGraph") -> dict:
        """Mirror the story's bible into world_cards[slug] so the book-shaped
        helpers (multi-voice segmentation, character portraits, anchors) work
        for stories too. Idempotent."""
        bible = (graph.meta or {}).get("bible") or {}
        if bible:
            self.state_data.setdefault("world_cards", {})[slug] = bible
        return bible

    def _view_dict(self, view, graph: "DecisionGraph", slug: str, history: list[str]) -> dict:
        node_data = view.data or {}
        node_id = view.node_id
        safe_slug = re.sub(r"[^\w-]", "_", slug)[:60]
        safe_node = re.sub(r"[^\w-]", "_", node_id)[:40]
        scene_url = self.state_data.get("scene_cache", {}).get(f"{slug}:{node_id}")
        crumbs = [
            (graph.nodes[h].data.get("title") or h)
            for h in history
            if h in graph.nodes
        ]
        return {
            "slug": slug,
            "node_id": node_id,
            "title": node_data.get("title", ""),
            "text": node_data.get("text", ""),
            "choices": [{"index": c.index, "to": c.to, "label": c.label} for c in view.choices],
            "status": view.status,
            "scene": scene_url if isinstance(scene_url, str) and scene_url.startswith("/reader/scene/") else None,
            "scene_file": f"{safe_slug}-n{safe_node}.png",
            "path": crumbs,
            "steps": len(history),
            "can_back": len((self._playthroughs().get(slug) or {}).get("undo") or []) > 0,
        }

    async def story_start(self, slug: str) -> dict:
        graph, _fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        self._prime_world_card(slug, graph)
        run = DecisionRun(graph)
        view = run.start()
        self._playthroughs()[slug] = {
            "run_state": run.to_state(),
            "undo": [],  # stack of prior run_states for rewind
            "started": datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        self.save_state(self.state_data)
        await self.emit("reader:story_started", {"slug": slug})
        return self._view_dict(view, graph, slug, run.history)

    async def story_choose(self, slug: str, choice) -> dict:
        graph, _fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        pt = self._playthroughs().get(slug)
        if not pt:
            return await self.story_start(slug)
        run = DecisionRun.from_state(graph, pt["run_state"])
        # Accept an integer index or a target node id.
        pick: int | str = choice
        if isinstance(choice, str) and choice.lstrip("-").isdigit():
            pick = int(choice)
        prev_state = pt["run_state"]
        try:
            view = run.choose(pick)
        except DecisionGraphError as e:
            return {"error": str(e)}
        # Push the pre-choice state so the reader can rewind one beat.
        undo = pt.setdefault("undo", [])
        undo.append(prev_state)
        if len(undo) > 100:
            del undo[:-100]
        pt["run_state"] = run.to_state()
        pt["updated"] = datetime.now(timezone.utc).isoformat()
        self.save_state(self.state_data)
        if view.status == "ended":
            await self.emit("reader:story_ended", {"slug": slug, "steps": len(run.history)})
        return self._view_dict(view, graph, slug, run.history)

    async def story_restart(self, slug: str) -> dict:
        self._playthroughs().pop(slug, None)
        self.save_state(self.state_data)
        return await self.story_start(slug)

    async def story_get(self, slug: str) -> dict:
        graph, fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        pt = self._playthroughs().get(slug)
        view = None
        if pt:
            run = DecisionRun.from_state(graph, pt["run_state"])
            try:
                view = self._view_dict(run.view(), graph, slug, run.history)
            except DecisionGraphError:
                view = None
        meta = graph.meta or {}
        learning = _story_learning_meta(graph, fm)
        return {
            "slug": slug,
            "title": meta.get("title") or fm.get("title") or slug,
            "lang": fm.get("lang") or "English",
            "cefr": fm.get("cefr") or "",
            "premise": meta.get("premise") or fm.get("premise") or "",
            "bible": meta.get("bible") or {},
            "target_words": learning["target_words"],
            "comprehension_checks": learning["comprehension_checks"],
            "in_progress": bool(pt),
            "view": view,
        }

    async def story_back(self, slug: str) -> dict:
        """Rewind one beat by popping the undo stack (robust — replays nothing)."""
        graph, _fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        pt = self._playthroughs().get(slug)
        if not pt or not pt.get("undo"):
            return {"error": "nothing to go back to"}
        pt["run_state"] = pt["undo"].pop()
        pt["updated"] = datetime.now(timezone.utc).isoformat()
        self.save_state(self.state_data)
        run = DecisionRun.from_state(graph, pt["run_state"])
        return self._view_dict(run.view(), graph, slug, run.history)

    async def story_history(self, slug: str) -> dict:
        """Return the full prose of every beat visited so far (re-read / 'story so
        far'). Content comes from the graph; the playthrough only stores the path."""
        graph, _fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        pt = self._playthroughs().get(slug)
        hist = ((pt or {}).get("run_state") or {}).get("history") or []
        beats = []
        for h in hist:
            n = graph.nodes.get(h)
            if n:
                beats.append({"node_id": h, "title": n.data.get("title", ""), "text": n.data.get("text", "")})
        return {"slug": slug, "beats": beats}

    async def story_to_game(self, slug: str) -> dict:
        """Turn a woven story into a playable top-down game via viz's game-2d shape.
        Builds a spatial brief from the story plus its CEFR/vocabulary/check contract and
        hands it to viz.generate. Optional integration — graceful if viz is absent."""
        graph, fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        brief, learning = _story_game_brief(graph, fm, slug)
        try:
            res = await self.call_app("viz", "generate", prompt=brief, shape="game-2d")
        except Exception as e:
            return {"error": f"game generation needs the viz app: {e}"}
        if isinstance(res, dict) and res.get("ok") and res.get("id"):
            await self.emit("reader:story_to_game", {"slug": slug, "viz_id": res["id"]})
            return {
                "ok": True,
                "viz_id": res["id"],
                "url": f"/viz/api/html/{res['id']}",
                "learning": learning,
            }
        return {"error": (res or {}).get("error", "game generation failed") if isinstance(res, dict) else "game generation failed"}

    # ── vocab-loop integration (English-learning track) ──────────

    async def _vocab_target_words(self, n: int = 8) -> list[str]:
        """Best-effort: today's vocab-loop feed words from the dictionary app, to
        weave into a story naturally. Fully guarded — absence is fine."""
        try:
            words = await self.call_app("dictionary", "_ensure_feed")
        except Exception:
            return []
        out: list[str] = []
        for w in words or []:
            if isinstance(w, dict) and (w.get("word") or "").strip():
                out.append(str(w["word"]).strip())
            elif isinstance(w, str) and w.strip():
                out.append(w.strip())
        return out[:n]

    # ── hub panel ────────────────────────────────────────────────

    async def panel_story_in_progress(self):
        pts = self._playthroughs()
        if not pts:
            return None
        # Newest by updated; only surface ones still running.
        rows = []
        for slug, pt in sorted(pts.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            if (pt.get("run_state") or {}).get("status") == "ended":
                continue
            graph, fm = self._load_story_graph(slug)
            if graph is None:
                continue
            title = (graph.meta or {}).get("title") or fm.get("title") or slug
            steps = len((pt.get("run_state") or {}).get("history") or [])
            rows.append({"title": title, "subtitle": f"{steps} beats in", "href": f"/reader/?story={slug}"})
            if len(rows) >= 3:
                break
        return rows or None

    # ── per-beat scene (reuses ScenesMixin building blocks) ──────

    async def generate_story_scene(self, slug: str, node_id: str, text: str, force: bool = False) -> dict:
        cache_key = f"{slug}:{node_id}"
        cache = self.state_data["scene_cache"]
        if not force:
            cached = cache.get(cache_key)
            if isinstance(cached, str) and cached.startswith("/reader/scene/"):
                return {"url": cached, "cached": True}
        graph, _fm = self._load_story_graph(slug)
        if graph is None:
            return {"error": f"story '{slug}' not found"}
        bible = (graph.meta or {}).get("bible", {}) or {}
        beat_text = (text or "").strip() or (graph.nodes.get(node_id).data.get("text", "") if node_id in graph.nodes else "")
        if not beat_text:
            return {"error": "no beat text to illustrate"}

        world_text = self._world_card_text(bible)
        anchors = self._present_character_anchors(bible, beat_text)
        # Context = the prose of the beats already visited (path history), so the
        # image stays continuous with where the reader has been.
        prior: list[str] = []
        pt = self._playthroughs().get(slug)
        if pt:
            for h in (pt.get("run_state", {}).get("history") or [])[-3:-1]:
                n = graph.nodes.get(h)
                if n and n.data.get("text"):
                    prior.append(n.data["text"][:300])

        sections: list[str] = []
        if world_text:
            sections.append("## World card\n" + world_text)
        if anchors:
            sections.append(
                "## Characters in THIS beat (use these phrases verbatim)\n"
                + "\n".join(f"- {a}" for a in anchors)
            )
        if prior:
            sections.append("## Earlier beats (context)\n" + "\n\n".join(prior))
        sections.append("## Current beat (the SUBJECT — visualize THIS)\n" + beat_text[:1500])

        try:
            prompt_for_image = await self.think(
                "\n\n".join(sections), system=SCENE_SYSTEM, domain="text", temperature=0.4
            )
        except Exception as e:
            return {"error": f"prompt generation failed: {e}"}
        style_hints = self.setting("reader.scene_style", "atmospheric, soft light, no text")
        full_prompt = f"{prompt_for_image.strip()}, {style_hints}"
        preset = self.setting("reader.scene_preset", "illustration") or ""
        draw_kwargs: dict = {}
        if preset:
            draw_kwargs["style"] = preset

        # Sustained-character-asset path: when a bible character is the subject of
        # this beat, generate (once) a canonical portrait and feed it as an
        # IP-Adapter reference so the SAME face recurs across beats — the closest
        # diffusion gets to a reusable asset (mirrors reader's book-scene path).
        filename = ""
        ipa_workflow = self.kernel.config.get("plugins.comfyui.ipadapter_workflow", "")
        if ipa_workflow:
            subject = self._character_in_text(beat_text, bible.get("characters") or [])
            if subject:
                portrait = await self._ensure_character_portrait(slug, subject)
                if portrait:
                    try:
                        comfyui = self.service("comfyui")
                        if comfyui and hasattr(comfyui, "upload_image") and hasattr(comfyui, "generate_from_workflow"):
                            ref_input = await comfyui.upload_image(str(portrait))
                            if ref_input:
                                filename = await comfyui.generate_from_workflow(
                                    workflow_key="ipadapter", prompt=full_prompt,
                                    image_filename=ref_input, seed=0, width=1024, height=1024,
                                )
                    except Exception as e:
                        log.warning("story ipadapter failed, falling back to bare draw: %s", e)
                        filename = ""

        if not filename:
            try:
                filename = await self.draw(full_prompt, **draw_kwargs)
            except Exception as e:
                log.warning("story scene generation failed: %s", e)
                return {"error": str(e)}
        if not filename:
            return {"error": "draw returned no image — is ComfyUI running?"}

        safe_slug = re.sub(r"[^\w-]", "_", slug)[:60]
        safe_node = re.sub(r"[^\w-]", "_", node_id)[:40]
        dest = self._scenes_dir() / f"{safe_slug}-n{safe_node}.png"
        if not await self._download_comfyui_image(str(filename), dest):
            return {"error": f"could not retrieve image '{filename}' from ComfyUI"}
        public_url = f"/reader/scene/{safe_slug}-n{safe_node}.png"
        cache[cache_key] = public_url
        self.save_state(self.state_data)
        await self.emit("reader:scene_generated", {"slug": slug, "node": node_id})
        return {"url": public_url, "prompt": full_prompt}

    # ── per-beat narration (multi-voice, reuses reader's segmenter) ──

    async def synthesize_story_beat(self, slug: str, text: str) -> dict:
        """Split a beat into narrator/dialogue segments and synthesize each with
        its voice. Reuses ScenesMixin/ReaderApp's quote-splitter + voice maps; the
        bible is primed into world_cards so character attribution works."""
        text = (text or "").strip()
        if not text:
            return {"segments": []}
        graph, _fm = self._load_story_graph(slug)
        if graph is not None:
            self._prime_world_card(slug, graph)
        segs = self._split_paragraph_segments(text, slug)
        out = []
        for s in segs:
            url = await self._speak_cached(s["text"], s.get("voice", ""))
            if url:
                out.append({**s, "url": url})
        return {"segments": out}

    # ── web routes ───────────────────────────────────────────────

    @web_route("GET", "/api/story-mode")
    async def api_story_mode(self, request):
        """Tiny status probe so the frontend knows whether to show story UI."""
        return {
            "enabled": self._story_enabled(),
            "default_lang": self.setting("reader.story.default_lang", "English"),
            "default_cefr": self.setting("reader.story.default_cefr", "C1"),
        }

    @web_route("GET", "/api/stories")
    async def api_stories(self, request):
        if not self._story_enabled():
            return {"stories": [], "disabled": True}
        return {"stories": self._list_stories()}

    @web_route("POST", "/api/story/weave")
    async def api_story_weave(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        try:
            data = await request.json()
            tw = data.get("target_words") or []
            if isinstance(tw, str):
                tw = [w for w in re.split(r"[,\n]", tw) if w.strip()]
            if data.get("use_vocab"):
                tw = list(dict.fromkeys(tw + await self._vocab_target_words()))
            return await self.weave_story(
                data.get("premise", ""),
                lang=data.get("lang") or "English",
                cefr=data.get("cefr") or "C1",
                target_words=tw,
                with_checks=bool(data.get("with_checks", False)),
            )
        except Exception as e:  # never 500 a user-facing generate route
            log.warning("weave route failed: %s", e)
            return {"error": f"weave failed: {e}"}

    @web_route("POST", "/api/story/suggest-premise")
    async def api_story_suggest_premise(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        try:
            return await self.suggest_premises()
        except Exception as e:
            log.warning("suggest-premise route failed: %s", e)
            return {"premises": [], "error": f"suggestion failed: {e}"}

    @web_route("GET", "/api/story/{slug}")
    async def api_story_get(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_get(request.path_params["slug"])

    @web_route("POST", "/api/story/{slug}/start")
    async def api_story_start(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_start(request.path_params["slug"])

    @web_route("POST", "/api/story/{slug}/choose")
    async def api_story_choose(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        data = await request.json()
        return await self.story_choose(request.path_params["slug"], data.get("choice"))

    @web_route("POST", "/api/story/{slug}/restart")
    async def api_story_restart(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_restart(request.path_params["slug"])

    @web_route("POST", "/api/story/{slug}/back")
    async def api_story_back(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_back(request.path_params["slug"])

    @web_route("GET", "/api/story/{slug}/history")
    async def api_story_history(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_history(request.path_params["slug"])

    @web_route("POST", "/api/story/{slug}/to-game")
    async def api_story_to_game(self, request):
        """Generate a playable game-2d artifact from this story (via viz)."""
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        return await self.story_to_game(request.path_params["slug"])

    @web_route("POST", "/api/story/{slug}/narrate")
    async def api_story_narrate(self, request):
        """Multi-voice narration of a beat (narrator vs dialogue voices)."""
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"segments": []}
        return await self.synthesize_story_beat(request.path_params["slug"], text)

    @web_route("POST", "/api/story/{slug}/scene")
    async def api_story_scene(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        data = await request.json()
        return await self.generate_story_scene(
            request.path_params["slug"],
            str(data.get("node", "")),
            data.get("text", ""),
            force=bool(data.get("force", False)),
        )

    @web_route("POST", "/api/story/speak")
    async def api_story_speak(self, request):
        if not self._story_enabled():
            return {"error": "story mode is disabled", "disabled": True}
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "no text"}
        url = await self._speak_cached(text[:2000])
        if not url:
            return {"error": "no local TTS provider available"}
        return {"audio": url, "status": "ok"}
