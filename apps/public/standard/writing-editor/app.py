"""Writing Editor — workplace draft revision with audience + tone awareness.

Paste a draft, pick an audience (peer / manager / external / report) and a
tone (default / direct / diplomatic / shorter), get back a revised version +
a list of edits with per-edit rationale + (optionally) one recurring-weakness
pattern the model spotted.

Revise returns a full rewrite *and* the individual edits behind it, each with
its own rationale. The page turns those into accept/reject chips and splices
only the accepted ones back into the draft — the model proposes, the writer
still owns every word.

"Save pattern as KB lesson" is still deferred. That ripple is the longer-term
value (calibrate the editor to the user's own recurring mistakes).

First consumer of the `[app] user_intent` data layer end-to-end — built
after the Feeling Lucky recommender surfaced "no app matches" for
'I want to improve my writing for office communication'.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import parse_llm_json, safe_note_filename
from emptyos.sdk import compose as _compose
from emptyos.sdk.doc_archetypes import (
    DOCUMENT_ARCHETYPES,
    archetype_style,
    get_archetype,
    scaffold_markdown,
)
from emptyos.sdk.vault_library import VaultLibrary

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def _word_count(text: str) -> int:
    """CJK-aware word count: each CJK char counts as one word."""
    cjk = len(_CJK_RE.findall(text))
    latin = len([w for w in _CJK_RE.sub(" ", text).split() if w])
    return cjk + latin


def _annotate_edits(draft: str, edits: list[dict]) -> list[dict]:
    """Mark each edit with `found`: does its `original` occur verbatim in the draft?

    The model is told to quote exact substrings, but it sometimes paraphrases —
    and an edit whose `original` can't be located can't be spliced back in. The
    flag lets the page pre-disable those rather than silently dropping them.
    Mutates and returns `edits`.
    """
    for e in edits:
        e["found"] = bool(e.get("original")) and e["original"] in draft
    return edits


class ArticleLibrary(VaultLibrary):
    tag = "article"
    fields = {
        "title": str,
        "status": str,
        "created": str,
        "updated": str,
        "words": int,
        "archetype": str,    # typed-document scaffold used at create (doc_archetypes)
        "pdf_theme": str,    # PDF_THEMES key pinned by the archetype; export prefers it
    }
    sort_key = "updated"
    sort_reverse = True
    search_fields = ["title"]
    fallback_folder = "30_Resources/EmptyOS/writing-editor/articles"


AUDIENCES = {
    "peer":     "collegial, direct, assumes shared context",
    "manager":  "clear ask, status visibility, no fluff",
    "external": "precise, professional, no internal jargon",
    "report":   "warm, specific, actionable (direct report)",
}

TONES = {
    "default":    "keep the user's existing tone, just polish for clarity",
    "direct":     "cut hedges, lead with the ask, no preamble",
    "diplomatic": "soften without being mealy — say the hard thing kindly",
    "shorter":    "target ~50% length, no information loss",
}


REVISE_SYSTEM = """You are an expert editor of workplace written communication.

The user message supplies one audience and one tone (with descriptions). Apply them.
Your job:
1. Revise the draft to fit that audience and tone, preserving the user's voice (not over-formal, not corporate-speak).
2. List every meaningful edit with a one-line rationale.
3. Optionally identify ONE recurring weakness in the user's writing (passive voice, hedging, burying the ask, etc.).

Output STRICT JSON. No markdown, no preface:

{
  "revised": "<full revised draft>",
  "edits": [
    {"original": "<exact substring from the draft>", "replacement": "<the new wording>", "rationale": "<one short sentence>"}
  ],
  "pattern": {"title": "<short noun phrase>", "note": "<one-paragraph lesson>"}
}

Rules:
- Do NOT add new information the user didn't write.
- Do NOT change first-person pronouns.
- Do NOT use corporate-speak ("synergy", "circle back", "leverage", "touch base").
- If the draft is already good, return it unchanged with edits: [] and pattern: null.
- `pattern` may be null when no recurring weakness is visible from one draft.
- `rationale` should be in the same language as the draft.
- Output ONLY the JSON object.
"""


MAX_DRAFT_LEN = 5000


# Opt-in prose lint — borrowed idea from stop-slop, graded into three severities.
# Reports findings; never rewrites (that's what /api/revise does). This is a
# separate verb on a separate button — it does NOT touch the user's default voice
# anywhere else in EmptyOS. See docs/OPEN-SOURCE-BORROWING-PLAN.md (S4).
LINT_SYSTEM = """You are a slop detector for written prose. You FIND problems; you do NOT rewrite the whole draft.

Scan the draft and flag spans matching the rules below, each at one of three severities:

hard-banned — slop tells that should almost always be cut:
- corporate-speak: synergy, leverage, circle back, touch base, deep dive, move the needle, low-hanging fruit, boil the ocean
- empty superlatives with no proof: revolutionary, game-changing, seamless, cutting-edge, world-class, best-in-class
- "it's not just X — it's Y" / "isn't just … it's …" constructions
- AI-essay filler openers: "In today's fast-paced world", "At the end of the day", "When it comes to"
- LLM tells: delve, tapestry, underscore (as verb), testament to, navigate the landscape, in conclusion

warn — usually weakens the writing; flag for the author to judge:
- hedging that buries the point: I think, I just, maybe, perhaps, somewhat, a bit, kind of
- vague intensifiers: very, really, quite, actually, basically
- em-dash overuse (more than ~1 per 2 sentences)
- passive voice that hides who acts ("it was decided")
- a list/claim with no supporting evidence or example

style — minor, take-it-or-leave-it:
- weak opening sentence that delays the point
- redundant phrasing ("in order to" → "to", "due to the fact that" → "because")
- nominalization ("make a decision" → "decide")

Output STRICT JSON. No markdown, no preface:

{
  "findings": [
    {"severity": "hard-banned", "span": "<exact substring from the draft>", "rule": "<short rule name>", "fix": "<a shorter rewrite or 'cut'>"}
  ]
}

Rules:
- `span` MUST be an exact substring copied from the draft.
- Order findings by position in the draft.
- If the draft is clean, return {"findings": []}.
- Do NOT invent problems to seem useful. A clean draft gets an empty list.
- Do NOT return a rewritten draft. Findings only.
- `fix` and `rule` should be in the same language as the draft.
- Output ONLY the JSON object.
"""

_LINT_SEVERITIES = {"hard-banned", "warn", "style"}


class WritingEditorApp(BaseApp):

    async def setup(self):
        await super().setup()
        self.articles = ArticleLibrary(self)

    @web_route("POST", "/api/revise")
    async def api_revise(self, request):
        body = await self.safe_json(request)
        draft = (body.get("draft") or "").strip()
        if not draft:
            return {"ok": False, "error": "draft required"}
        if len(draft) > MAX_DRAFT_LEN:
            return {"ok": False, "error": f"draft too long (>{MAX_DRAFT_LEN} chars)"}

        audience = str(body.get("audience") or self.app_config("default_audience", "peer")).strip()
        tone = str(body.get("tone") or self.app_config("default_tone", "default")).strip()
        if audience not in AUDIENCES:
            return {"ok": False, "error": f"unknown audience: {audience}"}
        if tone not in TONES:
            return {"ok": False, "error": f"unknown tone: {tone}"}

        prompt = (
            f"Audience: {audience} — {AUDIENCES[audience]}\n"
            f"Tone: {tone} — {TONES[tone]}\n\n"
            f"Draft:\n{draft}"
        )
        try:
            raw = await self.think(
                prompt,
                system=REVISE_SYSTEM,
                domain="text",
                temperature=0.3,
            )
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}

        parsed = parse_llm_json(raw) or {}
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "model returned non-object JSON", "raw": raw[:500]}

        revised = str(parsed.get("revised") or "").strip()
        edits = parsed.get("edits") if isinstance(parsed.get("edits"), list) else []
        pattern = parsed.get("pattern") if isinstance(parsed.get("pattern"), dict) else None

        clean_edits: list[dict] = []
        for e in edits:
            if not isinstance(e, dict):
                continue
            clean_edits.append({
                "original": str(e.get("original") or ""),
                "replacement": str(e.get("replacement") or ""),
                "rationale": str(e.get("rationale") or ""),
            })

        _annotate_edits(draft, clean_edits)

        await self.emit("writing-editor:revised", {
            "audience": audience,
            "tone": tone,
            "draft_len": len(draft),
            "edit_count": len(clean_edits),
            "found_count": sum(1 for e in clean_edits if e["found"]),
        })

        return {
            "ok": True,
            "audience": audience,
            "tone": tone,
            "revised": revised,
            "edits": clean_edits,
            "pattern": pattern,
        }

    @web_route("POST", "/api/lint")
    async def api_lint(self, request):
        """Opt-in prose lint — flag slop spans graded hard-banned / warn / style.

        Reports findings only; does not rewrite. Companion to /api/revise.
        """
        body = await self.safe_json(request)
        draft = (body.get("draft") or "").strip()
        if not draft:
            return {"ok": False, "error": "draft required"}
        if len(draft) > MAX_DRAFT_LEN:
            return {"ok": False, "error": f"draft too long (>{MAX_DRAFT_LEN} chars)"}

        try:
            raw = await self.think(
                f"Draft:\n{draft}",
                system=LINT_SYSTEM,
                domain="text",
                temperature=0.2,
            )
        except Exception as e:
            return {"ok": False, "error": f"think failed: {e}"}

        parsed = parse_llm_json(raw) or {}
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "model returned non-object JSON", "raw": raw[:500]}

        findings: list[dict] = []
        for f in parsed.get("findings") if isinstance(parsed.get("findings"), list) else []:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity") or "").strip().lower()
            if sev not in _LINT_SEVERITIES:
                sev = "style"
            span = str(f.get("span") or "")
            if not span:
                continue
            findings.append({
                "severity": sev,
                "span": span,
                "rule": str(f.get("rule") or ""),
                "fix": str(f.get("fix") or ""),
            })

        await self.emit("writing-editor:linted", {
            "draft_len": len(draft),
            "finding_count": len(findings),
        })

        return {"ok": True, "findings": findings}

    @web_route("GET", "/api/options")
    async def api_options(self, request):
        """Audiences + tones + their descriptions — drives the picker UI."""
        return {
            "audiences": [{"id": k, "desc": v} for k, v in AUDIENCES.items()],
            "tones": [{"id": k, "desc": v} for k, v in TONES.items()],
            "default_audience": self.app_config("default_audience", "peer"),
            "default_tone": self.app_config("default_tone", "default"),
            "max_draft_len": MAX_DRAFT_LEN,
        }

    # ── Compose — generate a draft from a message-kind playbook ──────────
    # Distinct from /api/revise (which transforms text you already wrote).
    # Engine: emptyos/sdk/compose.py, shared with the jobs + people apps.

    @web_route("GET", "/api/compose/kinds")
    async def api_compose_kinds(self, request):
        """Full message-kind playbook + channels + tones — drives the picker."""
        group = (request.query_params.get("group") or "").strip()
        return {
            "kinds": _compose.kinds_for(group),
            "channels": _compose.channels_meta(),
            "tones": _compose.tones_meta(),
        }

    @web_route("POST", "/api/compose")
    async def api_compose(self, request):
        """Generate a draft. Caller supplies kind + channel + context + freeform.

        Body: {kind, channel?, tone?, freeform?, variants?, context?: {label: value}}
        The general (ungrounded) entry point — jobs/people auto-build `context`
        from their vault notes; here the caller passes whatever it has.
        """
        body = await self.safe_json(request)
        kind = (body.get("kind") or "").strip()
        if not kind:
            return {"ok": False, "error": "kind required"}
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        # Tolerate loose recipient fields passed flat instead of in context.
        for label, key in (("Recipient", "to_name"), ("Their role", "to_role"),
                           ("Company", "company"), ("Mutual contact", "mutual")):
            v = (body.get(key) or "").strip()
            if v and label not in context:
                context[label] = v
        return await self.compose_message(
            kind,
            (body.get("channel") or "").strip(),
            context,
            tone=(body.get("tone") or "warm").strip(),
            freeform=(body.get("freeform") or "").strip(),
            variants=bool(body.get("variants")),
        )

    @cli_command("compose")
    async def cli_compose(
        self,
        kind: str = "",
        channel: str = "email",
        tone: str = "warm",
        about: str = "",
        to: str = "",
        company: str = "",
        variants: bool = False,
        json: bool = False,
    ):
        """eos compose --kind <kind> [--channel email|linkedin-dm|...] [--about "..."] [--json]

        Draft a professional/networking message from a Claude Code session or shell.
        `--kind` is required; list kinds with `eos compose` (no kind). `--about` is
        free-text grounding ("who, why, any facts"). `--json` emits the machine envelope.
        """
        if not kind:
            kinds = _compose.kinds_for()
            if json:
                self.print_json({"ok": False, "code": "invalid_args",
                                 "message": "--kind required", "data": {"kinds": kinds}})
                return
            self.print_rich("[bold]eos compose --kind <kind>[/bold]  — available kinds:")
            for k in kinds:
                self.print_rich(f"  [cyan]{k['id']}[/cyan]  ({k['group']}) — {k['intent']}")
            return

        context: dict = {}
        if to:
            context["Recipient"] = to
        if company:
            context["Company"] = company
        result = await self.compose_message(
            kind, channel, context, tone=tone, freeform=about, variants=variants,
        )
        if json:
            ok = bool(result.get("ok"))
            self.print_json({
                "ok": ok,
                "code": "ok" if ok else "error",
                "message": result.get("error") or f"{result.get('kind')} via {result.get('channel')}",
                "data": result if ok else None,
            })
            return
        if not result.get("ok"):
            self.print_rich(f"[red]✗[/red] {result.get('error')}")
            return
        if result.get("subject"):
            self.print_rich(f"[bold]Subject:[/bold] {result['subject']}")
        self.print_rich(f"\n{result['body']}\n")
        if result.get("why"):
            self.print_rich(f"[dim]Why this lands: {result['why']}[/dim]")
        if result.get("variant"):
            self.print_rich(f"\n[dim]— shorter variant —[/dim]\n{result['variant']}")

    # ── Documents — persistent large-print articles (vault-backed) ────────
    # Built for non-technical users (voice dictation + big fonts + PDF).
    # The Polish affordance reuses /api/revise — no separate AI verb.

    def _article_rel(self, filename: str) -> str:
        return self.vault_rel(self.vault_path(f"articles/{filename}"))

    def _article_reindex(self, filename: str):
        rel = self._article_rel(filename)
        if rel:
            self.vault_force_index(rel)

    @web_route("GET", "/api/articles")
    async def api_articles_list(self, request):
        return {"articles": self.articles.list()}

    async def panel_drafts_in_progress(self) -> list[dict] | None:
        """Hub glanceable — drafts still in progress, newest first.

        Returns None (drops the panel) when there's nothing in draft, so the
        hub stays quiet for users who don't write. Deep-links into the
        editor's hash route (/writing-editor/#<file>).
        """
        try:
            items = self.articles.list()
        except Exception:
            return None
        drafts = [a for a in items if (a.get("status") or "draft") == "draft"]
        if not drafts:
            return None
        rows = []
        for a in drafts[:5]:
            name = a.get("_vault_name") or a.get("file") or a.get("id") or ""
            words = a.get("words") or 0
            updated = a.get("updated") or ""
            sub = f"{words} words" + (f" · {updated}" if updated else "")
            rows.append({
                "title": a.get("title") or "Untitled draft",
                "subtitle": sub,
                "href": f"/writing-editor/#{name}" if name else "/writing-editor/",
                "icon": "✍️",
            })
        return rows

    @web_route("POST", "/api/articles")
    async def api_articles_create(self, request):
        data = await self.safe_json(request)
        title = (data.get("title") or "").strip()
        if not title:
            return {"error": "title is required"}
        filename = safe_note_filename(self.vault_dir / "articles", title, fallback_prefix="article")
        today = date.today().isoformat()
        # Optional typed-document scaffold (emptyos.sdk.doc_archetypes): seed the
        # body with a profile-valid skeleton + pin the matching PDF theme. Absent
        # or unknown → blank body, behaviour unchanged.
        archetype = (data.get("archetype") or "").lower().strip()
        body = ""
        fm = {
            "title": title,
            "status": "draft",
            "created": today,
            "updated": today,
            "words": 0,
            "tags": ["article"],
        }
        if archetype in DOCUMENT_ARCHETYPES:
            body = scaffold_markdown(archetype)
            fm["archetype"] = archetype
            fm["pdf_theme"] = archetype_style(archetype)
            fm["words"] = _word_count(body)
        self.vault_create_note(
            self._article_rel(filename) or f"30_Resources/EmptyOS/writing-editor/articles/{filename}",
            fm,
            body,
        )
        await self.emit("writing-editor:article_saved", {"file": filename, "title": title, "new": True})
        return {"ok": True, "file": filename}

    @web_route("GET", "/api/archetypes")
    async def api_archetypes(self, request):
        """Typed-document archetypes the create form can offer (id/title/description)."""
        return {
            "archetypes": [
                {"id": a.name, "title": a.title, "description": a.description}
                for a in DOCUMENT_ARCHETYPES.values()
            ]
        }

    @web_route("GET", "/api/articles/{file}")
    async def api_articles_detail(self, request):
        filename = request.path_params.get("file", "")
        item = self.articles.detail(filename)
        if not item:
            return {"error": "article not found"}
        item["_vault_path"] = self._article_rel(item["file"])
        return item

    @web_route("POST", "/api/articles/{file}")
    async def api_articles_save(self, request):
        filename = request.path_params.get("file", "")
        if not filename.endswith(".md"):
            filename += ".md"
        if not self.articles.find_file(filename):
            return {"error": "article not found"}
        data = await self.safe_json(request)

        body = data.get("body")
        meta: dict = {"updated": date.today().isoformat()}
        if body is not None:
            meta["words"] = _word_count(str(body))
        if data.get("title"):
            meta["title"] = str(data["title"]).strip()

        # Serialize the read-modify-write per article (journal _daily_lock
        # pattern) so a racing writer can't wipe this save. Emit stays outside.
        async with self.write_lock(f"article:{filename}"):
            if body is not None:
                self.articles.write_body(filename, str(body))
            self.articles.update(filename, meta)
        self._article_reindex(filename)
        await self.emit("writing-editor:article_saved", {"file": filename})
        return {"ok": True, "file": filename, "words": meta.get("words")}

    @web_route("DELETE", "/api/articles/{file}")
    async def api_articles_delete(self, request):
        filename = request.path_params.get("file", "")
        if not filename.endswith(".md"):
            filename += ".md"
        path = self.articles.find_file(filename)
        if not path:
            return {"error": "article not found"}
        path.unlink()
        self._article_reindex(filename)  # index_file on a missing file drops it
        return {"ok": True}

    @web_route("POST", "/api/transcribe")
    async def api_transcribe(self, request):
        """Voice dictation: multipart audio blob → text via the listen chain."""
        form = await request.form()
        audio = form.get("audio")
        if audio is None:
            return {"error": "no audio file"}
        try:
            filepath, _ = await self.save_audio_upload(audio, prefix="dictate")
            lang = str(self.setting("writing-editor.dictation_language", "zh") or "").strip()
            kwargs = {"language": lang} if lang else {}
            text = await self.listen(str(filepath), **kwargs)
            return {"text": text or ""}
        except Exception as e:
            return {"error": f"transcription failed: {e}"}

    @web_route("POST", "/api/articles/{file}/pdf")
    async def api_articles_pdf(self, request):
        filename = request.path_params.get("file", "")
        item = self.articles.detail(filename)
        if not item:
            return {"error": "article not found"}
        body = item.get("body") or ""  # detail() already strips frontmatter
        # If the body already opens with a fenced masthead (archetype scaffolds do),
        # it IS the masthead per the PDF profile — don't prepend a second one.
        if body.lstrip().startswith("```"):
            md = body
        else:
            md = (
                f"```\n{item.get('title') or Path(item['file']).stem}\n"
                f"{item.get('updated') or date.today().isoformat()}\n```\n\n"
                f"{body}"
            )
        stem = Path(item["file"]).stem
        out_rel = f"30_Resources/EmptyOS/writing-editor/exports/{stem}.pdf"
        # Prefer an archetype-pinned theme on the article; fall back to the app default.
        theme = item.get("pdf_theme") or self.setting("writing-editor.pdf_theme", "default")
        try:
            out = await self.render_pdf(md, out_rel, style=theme)
        except RuntimeError as e:
            return {"error": str(e)}
        rel = self.vault_rel(out) or out_rel
        await self.emit("writing-editor:article_exported", {"file": item["file"], "pdf": rel})
        return {"ok": True, "path": rel}
