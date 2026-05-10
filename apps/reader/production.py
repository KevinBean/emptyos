"""Production document mixin — Plot Writer + Storyboard Artist agents.

A markdown file in the vault holds the production team's output: Beats
(Plot Writer), Shots (Storyboard Artist), World card + Objects (Production
Designer), Cast (Casting Director), Cinematography (DP). The user can edit
any section directly in their vault viewer; the next render reads whatever's
there. Regenerating a section overwrites only that section, never the others
— your edits survive.
"""

from __future__ import annotations

import logging
import re

from emptyos.sdk import parse_frontmatter, strip_frontmatter

from ._helpers import DEFAULT_PRODUCTIONS_DIR, _split_paragraphs


log = logging.getLogger("emptyos.reader.production")


PLOT_WRITER_SYSTEM = """You are Marta Chen, a dramaturg with 20 years
adapting literary fiction for screen and stage. You are pragmatic and
structural — you separate the dramatic skeleton from the prose surface
and you don't mistake style for substance. You compress aggressively;
you'd rather have 8 strong beats than 20 weak ones.

You are extracting the narrative spine of a book for a reader app. Every
later production decision (which scenes to render, what each shot depicts)
flows from your beats — so be ruthless about what counts as a beat versus
what's atmospheric padding.

Output format — markdown, exactly this shape:

1. **Beat title** — ¶<start>–<end>
   One-sentence description of what happens, in present tense.

2. **Next beat title** — ¶<start>–<end>
   ...

Rules:
- Aim for 6-12 beats for a short story or novella, 12-25 for a novel.
- Each beat is a contiguous paragraph range — no overlaps, no gaps.
- Cover the entire book, start to end.
- A beat is a unit of dramatic action (an arrival, a confrontation, a
  decision, a turning point), not a unit of subject matter. Multiple
  paragraphs of dialogue serving the same dramatic function = one beat.
- Beat titles are evocative and short (3-7 words), not summaries.
- Description is one sentence, present tense, names the principal character
  and their action.

Do NOT:
- include text outside the numbered list
- wrap output in markdown fences
- add meta-commentary
- exceed the configured beat count for the book length
"""

STORYBOARD_SYSTEM = """You are Yuki Tanaka. 15 years storyboarding graphic
novel adaptations of literary fiction. Your aesthetic stance:

- You distrust over-cutting. Most adaptations break a quiet conversation
  into 12 shots; you'd shoot the same scene in 1 wide two-shot and let
  it breathe.
- You believe shot count should track dramatic density, not page count.
  A 40-paragraph dialogue at one location with two characters = 1 shot,
  not 40.
- You prefer wide establishing → medium → detail in that order,
  reserving close-ups for genuine moments of vulnerability or violence.
- You annotate framing as nouns: "wide establishing", "medium two-shot
  via blue plate", "interior detail", "low angle", "over-shoulder".
- You hate ornament. Your shot list is dry — no purple prose, no mood
  adjectives that belong in the cinematographer's section.

You receive: the book's beats (already written by Marta the dramaturg),
plus the prose itself with paragraph indices. Output a shot list where
each shot is a contiguous paragraph range with a single visual subject.

Output format — markdown, exactly this shape:

- shot 1 · <framing> · ¶<start>–<end> · "<one-clause subject>"
- shot 2 · <framing> · ¶<start>–<end> · "<one-clause subject>"
...

Rules:
- Shots must cover every paragraph with no gaps and no overlaps.
- Aim for one shot per dramatic location/configuration. A scene shift
  (location change, new character enters, time jump) = new shot.
- Pure dialogue between unchanging speakers in unchanging space = 1 shot.
- Subject is a single clause naming what's visually happening — not
  what's said. "Vashti and Kuno argue across the blue plate" not
  "Kuno asks his mother to visit".
- Total shots: roughly equal to the number of beats × 1.5, never more
  than 2× the beat count. If you find yourself making more, you're
  over-cutting.

Do NOT:
- Add prose outside the bullet list
- Wrap output in markdown fences
- Use emoji in framing labels
- Suggest visual style (palette, lighting) — that's the cinematographer's
  job, not yours
"""


class ProductionMixin:
    PRODUCTION_SECTIONS = ["Director's notes", "Beats", "Shots", "World card", "Objects", "Cast", "Cinematography"]

    def _production_path(self, slug: str) -> str:
        safe = re.sub(r"[^\w-]", "-", slug)[:60]
        base = self.vault_config("productions_dir", DEFAULT_PRODUCTIONS_DIR)
        return f"{base}/{safe}.md"

    @staticmethod
    def _parse_production_sections(body: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in body.split("\n"):
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m:
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = m.group(1).strip()
                buf = []
            else:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        return sections

    def _serialize_production(self, fm: dict, sections: dict[str, str]) -> str:
        ordered = [s for s in self.PRODUCTION_SECTIONS if s in sections]
        extra = [s for s in sections if s not in self.PRODUCTION_SECTIONS]
        body_parts: list[str] = []
        for s in ordered + extra:
            content = sections[s].strip()
            body_parts.append(f"## {s}\n\n{content}" if content else f"## {s}")
        fm_lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}:")
                for item in v:
                    fm_lines.append(f"  - {item}")
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        return "\n".join(fm_lines) + "\n\n" + "\n\n".join(body_parts) + "\n"

    async def _ensure_production_doc(self, slug: str) -> dict:
        """Read or create the production doc for *slug*. Returns parsed dict."""
        from datetime import date
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        rel = self._production_path(slug)
        try:
            existing = await self.read(rel)
        except Exception:
            existing = ""
        if existing:
            fm = parse_frontmatter(existing) or {}
            sections = self._parse_production_sections(strip_frontmatter(existing))
            return {"path": rel, "frontmatter": fm, "sections": sections, "fresh": False}
        meta, _body = self._read_book_body(book)
        fm = {
            "title": book["title"],
            "slug": slug,
            "author": meta.get("author", ""),
            "status": "draft",
            "last_built_by": "",
            "created": date.today().isoformat(),
            "updated": date.today().isoformat(),
            "tags": ["reader-production"],
        }
        sections = {s: "" for s in self.PRODUCTION_SECTIONS}
        self.vault_create_note(rel, frontmatter=fm, body=self._serialize_production({}, sections).split("---", 2)[-1].strip())
        return {"path": rel, "frontmatter": fm, "sections": sections, "fresh": True}

    async def update_production_section(self, slug: str, section: str, content: str) -> dict:
        """User edits a section. Overwrites only that section."""
        from datetime import date
        doc = await self._ensure_production_doc(slug)
        if "error" in doc:
            return doc
        sections = doc["sections"]
        sections[section] = content.strip()
        fm = doc["frontmatter"] or {}
        fm["updated"] = date.today().isoformat()
        fm["last_edited_section"] = section
        rel = doc["path"]
        full = self._serialize_production(fm, sections)
        await self.write(rel, full)
        await self.emit("reader:production_edited", {"slug": slug, "section": section})
        return {"ok": True, "section": section, "path": rel}

    async def regenerate_production_section(self, slug: str, section: str) -> dict:
        """Run the agent responsible for a section, overwrite that section."""
        if section == "Beats":
            return await self._run_plot_writer(slug)
        if section == "Shots":
            return await self._run_storyboard_artist(slug)
        return {"error": f"no agent yet for section '{section}'"}

    def _director_notes_block(self, doc: dict) -> str:
        """Return formatted Director's notes for prepending to any agent prompt."""
        sections = (doc or {}).get("sections", {}) or {}
        notes = (sections.get("Director's notes") or "").strip()
        if not notes:
            return ""
        return (
            "## Director's notes (the user has written these — honor them)\n\n"
            + notes
            + "\n\n"
        )

    async def _run_plot_writer(self, slug: str) -> dict:
        """Plot Writer agent: prose → Beats. Writes the ## Beats section only."""
        from datetime import date
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        meta, body = self._read_book_body(book)
        paragraphs = _split_paragraphs(body)
        digest_lines = []
        budget = 60_000
        used = 0
        for i, p in enumerate(paragraphs):
            if p.startswith("##"):
                continue
            line = f"[{i}] {p[:500]}"
            if used + len(line) > budget:
                break
            digest_lines.append(line)
            used += len(line)
        target_count = "12-20" if len(paragraphs) > 100 else "6-12"
        doc = await self._ensure_production_doc(slug)
        director_notes = self._director_notes_block(doc)
        user_msg = (
            director_notes
            + f"Book: {book['title']}\n"
            + f"Author: {meta.get('author', 'unknown')}\n"
            + f"Total paragraphs: {len(paragraphs)}\n"
            + f"Target beat count: {target_count}\n\n"
            + "Numbered paragraphs:\n\n" + "\n\n".join(digest_lines)
        )
        try:
            beats_md = await self.think(
                user_msg, system=PLOT_WRITER_SYSTEM, domain="reason", temperature=0.3
            )
        except Exception as e:
            log.warning("plot writer failed: %s", e)
            return {"error": f"plot writer LLM call failed: {e}"}
        beats_md = (beats_md or "").strip()
        beats_md = re.sub(r"^```(?:markdown)?\n?", "", beats_md)
        beats_md = re.sub(r"\n?```$", "", beats_md)

        sections = doc["sections"]
        sections["Beats"] = beats_md
        fm = doc["frontmatter"] or {}
        fm["last_built_by"] = "plot-writer (Marta)"
        fm["updated"] = date.today().isoformat()
        full = self._serialize_production(fm, sections)
        await self.write(doc["path"], full)
        await self.emit("reader:production_section_built", {"slug": slug, "section": "Beats", "agent": "plot-writer"})
        return {"ok": True, "section": "Beats", "path": doc["path"], "preview": beats_md[:400]}

    async def _run_storyboard_artist(self, slug: str) -> dict:
        """Storyboard Artist agent (Yuki): Beats + prose → Shots."""
        from datetime import date
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        doc = await self._ensure_production_doc(slug)
        if "error" in doc:
            return doc
        beats = (doc["sections"].get("Beats") or "").strip()
        if not beats:
            return {"error": "Beats not yet written. Run the Plot Writer first."}

        meta, body = self._read_book_body(book)
        paragraphs = _split_paragraphs(body)
        digest_lines = []
        budget = 50_000
        used = 0
        for i, p in enumerate(paragraphs):
            if p.startswith("##"):
                continue
            line = f"[{i}] {p[:400]}"
            if used + len(line) > budget:
                break
            digest_lines.append(line)
            used += len(line)

        director_notes = self._director_notes_block(doc)
        user_msg = (
            director_notes
            + f"Book: {book['title']}\n"
            + f"Total paragraphs: {len(paragraphs)}\n\n"
            + "## Beats (already written by Marta the dramaturg — your shots should respect these)\n\n"
            + beats + "\n\n"
            + "## Numbered paragraphs\n\n" + "\n\n".join(digest_lines)
        )
        try:
            shots_md = await self.think(
                user_msg, system=STORYBOARD_SYSTEM, domain="reason", temperature=0.3
            )
        except Exception as e:
            log.warning("storyboard artist failed: %s", e)
            return {"error": f"storyboard LLM call failed: {e}"}
        shots_md = (shots_md or "").strip()
        shots_md = re.sub(r"^```(?:markdown)?\n?", "", shots_md)
        shots_md = re.sub(r"\n?```$", "", shots_md)

        sections = doc["sections"]
        sections["Shots"] = shots_md
        fm = doc["frontmatter"] or {}
        fm["last_built_by"] = "storyboard-artist (Yuki)"
        fm["updated"] = date.today().isoformat()
        full = self._serialize_production(fm, sections)
        await self.write(doc["path"], full)
        await self.emit("reader:production_section_built", {"slug": slug, "section": "Shots", "agent": "storyboard-artist"})
        return {"ok": True, "section": "Shots", "path": doc["path"], "preview": shots_md[:400]}
