"""Publish — AI writing surface + raw note load/save/toggle.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the `/api/ai-write` LLM action router (polish / expand /
compress / translate / outline / suggest_title / review / adapt_linkedin),
the per-site voice-guide injection (`_voice_block`
— reads `<source_folder>/_voice.md`, presence-gated, see its docstring),
topic suggestion, and the raw-markdown note plumbing the writer panel
needs (`save_draft`, `/api/save-draft`, `/api/load-post`, `/api/toggle-publish`).

Functions here are bound onto PublishApp as methods (see the wiring section
in app.py), so they receive `self` as their first argument and use BaseApp
helpers (`self.think`, `self._vault_dir`, `self._source_folder`, …) directly.

Reaches into other modules: none. Do not import from `.app` (it imports us,
which would cycle).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import (
    parse_frontmatter,
    parse_llm_json,
    slugify,
    strip_frontmatter,
)

from .prompts import PROMPTS

# Markdown image / Obsidian embed syntax. Stripped from an essay before it's
# adapted for social so a figure reference can't leak into the post text (the
# social platforms carry no such image; the alt/caption is not the argument).
_IMG_MARKDOWN = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")


def _strip_media(text: str) -> str:
    """Drop image embeds and collapse the blank lines they leave behind."""
    out = _IMG_MARKDOWN.sub("", text or "")
    # Collapse 3+ newlines (left where an image sat on its own line) to 2.
    return re.sub(r"\n{3,}", "\n\n", out).strip()

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401 — for type hints only


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   api_ai_write        = _writer.api_ai_write
#   adapt_post          = _writer.adapt_post
#   api_toggle_publish  = _writer.api_toggle_publish
#   save_draft          = _writer.save_draft
#   api_save_draft      = _writer.api_save_draft
#   api_load_post       = _writer.api_load_post
#   api_suggest_topics  = _writer.api_suggest_topics
#   _voice_block        = _writer._voice_block
#   api_voice_status    = _writer.api_voice_status
#   apply_linkedin_playbook = _writer.apply_linkedin_playbook
#   linkedin_voice_playbook = _writer.linkedin_voice_playbook
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# platform → the registered voice-aware adaptation prompt. adapt_post is the
# single cross-app entry point (promote's distribution engine consumes it via
# call_app); api_ai_write's adapt_* actions delegate here so the recipe lives
# in exactly one place.
_ADAPT_PROMPTS = {
    "linkedin": "adapt_linkedin_prompt",
    "x": "adapt_x_prompt",
    "reddit": "adapt_reddit_prompt",
}

_LINKEDIN_PLAYBOOK_BEGIN = "<!-- promote:linkedin-playbook:start -->"
_LINKEDIN_PLAYBOOK_END = "<!-- promote:linkedin-playbook:end -->"


def _voice_playbook_section(text: str) -> str:
    """Return only the managed LinkedIn playbook body from a voice note."""
    if _LINKEDIN_PLAYBOOK_BEGIN not in text or _LINKEDIN_PLAYBOOK_END not in text:
        return ""
    middle = text.split(_LINKEDIN_PLAYBOOK_BEGIN, 1)[1].split(
        _LINKEDIN_PLAYBOOK_END, 1
    )[0].strip()
    lines = middle.splitlines()
    if lines and lines[0].strip().lower() == "## linkedin performance playbook":
        lines = lines[1:]
    return "\n".join(lines).strip()


def _replace_voice_playbook(text: str, markdown: str) -> str:
    """Replace the one managed block while preserving every human-owned byte."""
    clean = (markdown or "").replace(_LINKEDIN_PLAYBOOK_BEGIN, "")
    clean = clean.replace(_LINKEDIN_PLAYBOOK_END, "").strip()
    block = (
        _LINKEDIN_PLAYBOOK_BEGIN
        + "\n## LinkedIn performance playbook\n"
        + clean
        + "\n"
        + _LINKEDIN_PLAYBOOK_END
    )
    if _LINKEDIN_PLAYBOOK_BEGIN in text and _LINKEDIN_PLAYBOOK_END in text:
        before, tail = text.split(_LINKEDIN_PLAYBOOK_BEGIN, 1)
        _, after = tail.split(_LINKEDIN_PLAYBOOK_END, 1)
        pieces = [before.rstrip(), block]
        if after.strip():
            pieces.append(after.strip())
        return "\n\n".join(piece for piece in pieces if piece) + "\n"
    base = text.rstrip() or "# Voice guide"
    return base + "\n\n" + block + "\n"



async def adapt_post(self, platform: str = "", text: str = "", parent: str = "") -> dict:
    """Adapt a finished blog post into a per-platform social draft.

    Public cross-app method (call_app target) — promote's distribution engine
    passes the essay body + platform and stages the returned draft as a review
    card. Injects the active site's voice guide (`_voice_block`) so an X thread
    or Reddit post carries the same voice as the LinkedIn recipe.

    Returns ``{ok, text, platform, provenance}`` or ``{error}`` — never raises,
    so a single platform's failure doesn't abort a multi-platform run.
    """
    platform = (platform or "").strip().lower()
    text = (text or "").strip()
    prompt_key = _ADAPT_PROMPTS.get(platform)
    if not prompt_key:
        return {"error": f"Unknown platform: {platform or '(none)'}"}
    if not text:
        return {"error": "No text provided"}
    try:
        voice = await self._voice_block()
        parent = (parent or "").strip()
        user_msg = ""
        if parent:
            user_msg += f"Parent post file (for the parent_post frontmatter field): {parent}\n\n"
        # Strip image embeds so a figure reference can't ride into the social copy.
        user_msg += "Article:\n\n" + _strip_media(text)
        result = await self.think(
            user_msg,
            domain="text",
            system=getattr(PROMPTS, prompt_key) + voice,
            temperature=0.5,
        )
    except Exception as e:
        return {"error": f"adapt failed: {e}"}
    return {
        "ok": True,
        "text": result,
        "platform": platform,
        "provenance": self.last_provenance(),
    }


async def _voice_block(self) -> str:
    """Active site's voice guide as a system-prompt suffix; "" when absent.

    Reads ``<vault>/<source_folder>/_voice.md`` per call (user-triggered,
    low-frequency) via the read capability. The ``_`` filename prefix keeps
    the note out of ``SiteBuilder.scan()`` (published site, Drafts, topic
    suggestions), so it can never leak to the public site. Presence-gated by
    design: no note → "" → every prompt byte-identical to the constants in
    prompts.py; creating the vault note IS the opt-in, there is no toml flag.

    Graduation trigger (Dev Rule 9): when a second consumer wants the same
    read (promote's LinkedIn drafter, the eos-devlog-publish skill), extract
    to an emptyos/sdk/ helper instead of copying this.
    """
    vault = self._vault_dir()
    source = self._source_folder()
    if not vault or not source:
        return ""
    note = Path(vault) / source / "_voice.md"
    if not note.exists():
        return ""
    try:
        body = strip_frontmatter(await self.read(str(note))).strip()
    except Exception:
        return ""
    if not body:
        return ""
    return (
        "\n\n## Author voice guide\n"
        "This site's author has a defined voice. Match it in anything you "
        "write or revise; when reviewing, judge the text against it:\n\n"
        + body[:6000]
    )

async def linkedin_voice_playbook(self) -> dict:
    """Read the managed playbook section from the active site's voice note."""
    vault = self._vault_dir()
    source = self._source_folder()
    rel = f"{source}/_voice.md" if source else ""
    if not vault or not source:
        return {"exists": False, "path": rel, "markdown": ""}
    note = Path(vault) / source / "_voice.md"
    if not note.exists():
        return {"exists": False, "path": rel, "markdown": ""}
    try:
        text = await self.read(str(note))
    except Exception:
        return {"exists": True, "path": rel, "markdown": ""}
    return {"exists": True, "path": rel, "markdown": _voice_playbook_section(text)}


async def apply_linkedin_playbook(
    self, markdown: str = "", source_proposal: str = ""
) -> dict:
    """Apply one reviewed playbook to the owned voice note under a note lock."""
    markdown = (markdown or "").strip()
    if not markdown:
        return {"error": "Playbook cannot be empty."}
    vault = self._vault_dir()
    source = self._source_folder()
    if not vault or not source:
        return {"error": "The active publish site has no vault source folder."}
    rel = f"{source}/_voice.md"
    note = Path(vault) / rel
    async with self.note_lock(rel):
        try:
            current = await self.read(str(note)) if note.exists() else "# Voice guide\n"
            updated = _replace_voice_playbook(current, markdown)
            await self.write(str(note), updated)
        except Exception as exc:
            return {"error": f"Could not write the active site voice guide: {exc}"}
    return {
        "ok": True,
        "path": rel,
        "site": self._active_site_id(),
        "source_proposal": source_proposal,
    }



@web_route("GET", "/api/voice-status")
async def api_voice_status(self, request):
    """Whether the active site has a voice note — feeds the writer's voice chip."""
    vault = self._vault_dir()
    source = self._source_folder()
    rel = f"{source}/_voice.md" if source else ""
    exists = bool(vault and source and (Path(vault) / source / "_voice.md").exists())
    return {"exists": exists, "path": rel, "site": self._active_site_id()}


@web_route("POST", "/api/ai-write")
async def api_ai_write(self, request):
    """AI writing actions: polish, expand, compress, translate, outline, suggest_title, review."""
    data = await request.json()
    action = data.get("action", "")
    text = (data.get("text") or "").strip()
    if not text:
        return {"error": "No text provided"}

    # Active site's voice guide — "" when no _voice.md exists (prompts then
    # stay byte-identical to the constants). Injected only into prose-shaping
    # actions; translate/outline/suggest_* stay voice-neutral.
    voice = await self._voice_block()

    if action == "review":
        focus = (data.get("focus") or "").strip()
        user_msg = ""
        if focus:
            user_msg += f"**Additional focus for this review:** {focus}\n\n"
        user_msg += "Article:\n\n" + text
        result = await self.think(
            user_msg,
            domain="text",
            system=PROMPTS.review_prompt_header + voice,
            temperature=0.4,
        )
        return {"review": result, "action": "review", "provenance": self.last_provenance()}

    # Social adaptations (linkedin / x / reddit) all delegate to adapt_post so
    # the recipe lives in one place; the response shape here stays as the
    # writer panel expects ({text, action, provenance}).
    if action.startswith("adapt_"):
        platform = action[len("adapt_"):]
        res = await self.adapt_post(platform=platform, text=text, parent=data.get("parent", ""))
        if res.get("error"):
            return {"error": res["error"]}
        return {"text": res["text"], "action": action, "provenance": res.get("provenance")}

    prompts = {
        "polish": PROMPTS.polish_prompt + text,
        "expand": PROMPTS.expand_prompt + text,
        "compress": PROMPTS.compress_prompt + text,
        "translate": PROMPTS.translate_prompt + text,
        "outline": PROMPTS.outline_prompt + text,
    }

    if action == "suggest_title":
        result = await self.think(
            PROMPTS.suggest_title_prompt + text[:3000],
            system=PROMPTS.writer_system,
            domain="text",
            temperature=0.4,
        )
        parsed = parse_llm_json(result, {"title": "", "summary": ""})
        parsed["provenance"] = self.last_provenance()
        return parsed

    prompt = prompts.get(action)
    if not prompt:
        return {"error": f"Unknown action: {action}"}

    system = PROMPTS.writer_system + (voice if action in {"polish", "expand", "compress"} else "")
    result = await self.think(prompt, system=system, domain="text", temperature=0.5)
    return {"text": result, "action": action, "provenance": self.last_provenance()}


@web_route("POST", "/api/toggle-publish")
async def api_toggle_publish(self, request):
    """Flip frontmatter publish: between true/false for a vault note."""
    data = await request.json()
    raw_path = (data.get("path") or "").strip()
    target = bool(data.get("publish"))
    if not raw_path:
        return {"error": "path is required"}

    vault = self._vault_dir()
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = Path(vault) / raw_path

    try:
        file_path.resolve().relative_to(Path(vault).resolve())
    except ValueError:
        return {"error": "Path outside vault"}
    if not file_path.exists():
        return {"error": "File not found"}

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {"error": "Note has no frontmatter"}

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {"error": "Malformed frontmatter"}

    new_value = "true" if target else "false"
    replaced = False
    for i in range(1, end_idx):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("publish:"):
            indent = line[: len(line) - len(stripped)]
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"{indent}publish: {new_value}{newline}"
            replaced = True
            break
    if not replaced:
        lines.insert(end_idx, f"publish: {new_value}\n")

    file_path.write_text("".join(lines), encoding="utf-8")
    # Mirror the file into posts/drafts/ or posts/published/ to match the new
    # flag (no-op when the site keeps posts flat). Fail-soft — the flag flip
    # above already succeeded; the move only tidies the location.
    new_path = self._mirror_post_location(file_path, target)
    return {"ok": True, "path": str(new_path), "publish": target}


async def save_draft(self, title: str = "", content: str = "", path: str = "") -> dict:
    """Save content to vault as a markdown file. Public method for
    call_app — used by `apps/company/` scenario deliverables. The web
    route below is a thin wrapper that unpacks the request body."""
    title = (title or "").strip()
    if not title:
        return {"error": "Title is required"}

    if path and Path(path).exists():
        Path(path).write_text(content or "", encoding="utf-8")
        return {"ok": True, "path": path}

    # New posts are drafts by definition → posts/drafts/ (or the posts/ root
    # when the site keeps posts flat). The publish flag stays authoritative;
    # this only decides the initial location.
    folder = self._new_post_dir()
    folder.mkdir(parents=True, exist_ok=True)

    slug = slugify(title)
    file_path = folder / f"{slug}.md"

    counter = 1
    while file_path.exists():
        file_path = folder / f"{slug}-{counter}.md"
        counter += 1

    file_path.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": str(file_path)}


@web_route("POST", "/api/save-draft")
async def api_save_draft(self, request):
    """Save content to vault as a markdown file."""
    data = await request.json()
    return await self.save_draft(
        title=data.get("title", ""),
        content=data.get("content", ""),
        path=data.get("path", ""),
    )


@web_route("GET", "/api/load-post")
async def api_load_post(self, request):
    """Load raw markdown content of a vault note."""
    path = request.query_params.get("path", "")
    if not path:
        return {"error": "path is required"}

    vault = self._vault_dir()
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = Path(vault) / path

    try:
        file_path.resolve().relative_to(Path(vault).resolve())
    except ValueError:
        return {"error": "Path outside vault"}

    if not file_path.exists():
        return {"error": "File not found"}

    content = file_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    body = strip_frontmatter(content).strip()

    # Preserve the raw frontmatter block so the writer doesn't drop fields it
    # doesn't render (cover, featured, custom).
    raw_fm = ""
    if content.startswith("---\n"):
        close = content.find("\n---\n", 4)
        if close != -1:
            raw_fm = content[4:close]

    return {
        "content": body,
        "title": fm.get("title", file_path.stem.replace("-", " ").title()),
        "tags": fm.get("tags", []),
        "type": fm.get("type", "post"),
        "summary": fm.get("summary", ""),
        "image_prompt": fm.get("image_prompt", ""),
        "raw_frontmatter": raw_fm,
        "path": str(file_path),
    }


@web_route("POST", "/api/suggest-topics")
async def api_suggest_topics(self, request):
    """AI scans vault and suggests content worth publishing."""
    vault = self._vault_dir()
    if not vault:
        return {"error": "No vault configured"}

    scan_raw = self.vault_config("scan_folders", "30_Resources,20_Areas,10_Projects")
    scan_folders = [f.strip() for f in scan_raw.split(",")]
    candidates = []

    for folder in scan_folders:
        folder_path = Path(vault) / folder
        if not folder_path.exists():
            continue
        for md in folder_path.rglob("*.md"):
            if md.name.startswith(("_", ".", "%", "$")):
                continue
            try:
                content = md.read_text(encoding="utf-8")[:500]
            except Exception:
                continue
            fm = parse_frontmatter(content)
            if str(fm.get("publish", "")).lower() in ("true", "yes"):
                continue
            tags = fm.get("tags", [])
            if isinstance(tags, list) and "private" in tags:
                continue
            title = fm.get("title", md.stem.replace("-", " "))
            candidates.append(f"- {title} ({md.relative_to(Path(vault))})")

    if not candidates:
        return {"topics": [], "message": "No candidates found"}

    sample = "\n".join(candidates[:50])

    result = await self.think(
        PROMPTS.suggest_topics_prompt + sample,
        system=PROMPTS.writer_system,
        domain="text",
        temperature=0.4,
    )

    topics = parse_llm_json(result, [])
    return {
        "topics": topics if isinstance(topics, list) else [],
        "provenance": self.last_provenance(),
    }
