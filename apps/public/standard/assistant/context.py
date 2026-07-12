"""Assistant — chat messages + system prompt + vault context retrieval.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: turning session history + retrieved vault snippets into the
OpenAI-style messages list; building the full system prompt (base + wheel
+ user-state dossier + per-session custom); deciding which questions
trigger vault retrieval and producing the snippets.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.call_app`` for cross-app state reads
(projects, jobs, journal, search).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from emptyos.runtime import wheel as _wheel

from .prompts import (
    MAX_CHAT_TURNS,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_FILES,
    SYSTEM_PROMPT,
)

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   _build_chat_messages    = _context._build_chat_messages
#   _build_user_state       = _context._build_user_state
#   _build_system           = _context._build_system
#   _should_skip_retrieval  = _context._should_skip_retrieval
#   _build_context          = _context._build_context
#   _retrieval_query        = _context._retrieval_query
#   _collect_context        = _context._collect_context  # whole-vault ctx + source paths
#   _scoped_context         = _context._scoped_context   # two-phase companion (ctx, paths)
#   _full_answer            = _context._full_answer       # two-phase companion (text, ctx, paths)
#   _reconcile              = _context._reconcile         # recency-aware 4-verdict
#   _note_date              = _context._note_date         # best date for a note
#   _provenance             = _context._provenance        # dated source labels (prompt)
#   _provenance_items       = _context._provenance_items  # structured {name,date,path} (UI)
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


def _build_chat_messages(
    self, session: dict, vault_context: str = "", max_turns: int = MAX_CHAT_TURNS
) -> list[dict]:
    """Build an OpenAI-style messages list from session history.

    - Keeps the last `max_turns` entries from DB.
    - Drops slash-command meta replies (stored with agent='system').
    - Injects vault_context into the final user turn so the model sees it
      clearly tied to the current question without polluting prior turns.
    """
    raw = session.get("messages", [])[-max_turns:]
    msgs: list[dict] = []
    for m in raw:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and m.get("agent") == "system":
            continue
        content = m.get("text", "")
        if not content:
            continue
        msgs.append({"role": role, "content": content})

    if vault_context and msgs and msgs[-1]["role"] == "user":
        msgs[-1] = {
            "role": "user",
            "content": (
                "[Possibly-relevant vault snippets — reference only. "
                "Ignore them unless they directly answer the user's question below. "
                "Do NOT assume the user is asking about these topics.]\n"
                f"{vault_context}\n\n"
                f"[User's actual message]\n{msgs[-1]['content']}"
            ),
        }
    return msgs


async def _build_user_state(self) -> str:
    """Compact 'who you are right now' snapshot injected into every chat turn.

    Pulls active projects, active job applications, today's journal summary.
    Each source is fail-soft — an uninstalled or erroring app drops its
    section silently. Cached 60s because the facts change slowly and this
    runs on every message.
    """
    now = datetime.now().timestamp()
    if self._state_cache is not None and (now - self._state_cache_ts) < 60:
        return self._state_cache

    lines: list[str] = []

    try:
        projects = await self.call_app("projects", "list_projects", status_filter="active")
        if projects:
            lines.append("Active projects:")
            for p in projects[:6]:
                name = p.get("name") or p.get("id", "")
                dl = f" due {p['deadline']}" if p.get("deadline") else ""
                tasks = ""
                if p.get("total_tasks"):
                    tasks = f" [{p.get('done_tasks', 0)}/{p['total_tasks']}]"
                lines.append(f"  - {name}{tasks}{dl}")
    except Exception:
        pass

    try:
        summary = await self.call_app("jobs", "get_summary")
        by_status = (summary or {}).get("applications", {}).get("by_status", {})
        closed = {"rejected", "withdrawn", "not_pursuing", "accepted"}
        active = {k: v for k, v in by_status.items() if k not in closed}
        if active:
            total = sum(active.values())
            parts = ", ".join(f"{v} {k}" for k, v in active.items())
            lines.append(f"Job applications in flight: {total} ({parts})")
    except Exception:
        pass

    try:
        j = await self.call_app("journal", "get_summary")
        if j:
            bits = []
            if j.get("today_entries"):
                bits.append(f"{j['today_entries']} entries today")
            if j.get("streak"):
                bits.append(f"{j['streak']}-day streak")
            if j.get("mood"):
                bits.append(f"mood={j['mood']}")
            if bits:
                lines.append("Journal: " + ", ".join(bits))
    except Exception:
        pass

    state = "\n".join(lines)
    self._state_cache = state
    self._state_cache_ts = now
    return state


async def _build_system(self, session: dict | None = None) -> str:
    """Full system prompt: base + wheel + user-state dossier + session-custom."""
    system = SYSTEM_PROMPT.format(date=date.today().isoformat())
    system += _wheel.planner_context(self.kernel)
    state = await self._build_user_state()
    if state:
        system += f"\n\nCurrent user state:\n{state}"
    if session:
        project_id = session.get("project_id") or ""
        if project_id:
            max_chars = int(self.app_config("max_project_context_chars", 30_000))
            try:
                project_ctx = await self.call_app(
                    "projects",
                    "load_project_context",
                    project_id=project_id,
                    max_chars=max_chars,
                )
            except Exception:
                project_ctx = ""
            if project_ctx:
                system += (
                    "\n\nPinned project context — every reply in this session "
                    f"should be informed by the following project documents "
                    f"(project id: {project_id}):\n\n{project_ctx}"
                )
        custom = session.get("system_prompt", "")
        if custom:
            system += f"\n\nCustom instructions: {custom}"
    return system


def _should_skip_retrieval(self, question: str) -> bool:
    """Skip vault retrieval for greetings / very short / stopword-only queries.

    Short generic queries ("hello", "hi", "thanks", "ok") match thousands of
    lines across the vault and produce noisy context that hijacks the reply.
    Heuristic: under 12 chars or only stopwords → skip.
    """
    q = (question or "").strip().lower().rstrip("?.!,")
    if len(q) < 12:
        return True
    tokens = [t for t in q.replace("?", " ").replace("!", " ").split() if t]
    stop = {
        "hi", "hello", "hey", "yo", "sup", "hola",
        "thanks", "thank", "you",
        "ok", "okay", "cool", "nice", "great", "good",
        "morning", "evening", "afternoon", "night",
        "bye", "gm", "gn", "lol", "haha", "?",
        "test", "ping", "yes", "no", "sure", "wassup",
        "how", "are", "doing",
    }
    if tokens and all(t in stop for t in tokens):
        return True
    return False


def _retrieval_query(self, question: str, session: dict | None = None) -> str:
    """Multi-turn retrieval query: fold recent prior turns into the search query
    so a follow-up like "how does that work?" doesn't lose topic. Session messages
    use {role, text}; map to {role, content} for the helper."""
    if session and session.get("messages"):
        from emptyos.sdk.embeddings import build_retrieval_query

        history = [
            {"role": m.get("role", ""), "content": m.get("text", "")}
            for m in session["messages"]
            if m.get("text")
        ]
        if history and history[-1].get("content") == question:
            history = history[:-1]
        return build_retrieval_query(history, question)
    return question


async def _collect_context(
    self,
    question: str,
    session: dict | None = None,
    *,
    max_files: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, list[str]]:
    """Whole-vault retrieval → (formatted context, source note paths). The paths
    let the recency-aware reconcile attribute each answer to dated source notes."""
    if self._should_skip_retrieval(question):
        return "", []
    retrieval_query = self._retrieval_query(question, session)
    n_files = max_files or MAX_CONTEXT_FILES
    n_chars = max_chars or MAX_CONTEXT_CHARS

    # Prefer embedding-based search when available — much higher recall
    # on paraphrase queries. Falls back to grep on any failure or when
    # embeddings unavailable. Routed through apps/search so a single
    # retrieval pipeline serves both /search and the assistant.
    results: list = []
    try:
        if self.embeddings_available:
            resp = await self.call_app(
                "search", "_embed_search",
                query=retrieval_query, top=n_files,
            )
            if isinstance(resp, tuple) and len(resp) >= 1:
                paths = resp[0] or []
                results = [{"path": p} for p in paths]
    except Exception:
        results = []
    if not results:
        try:
            results = await self.search(question, path=str(self.kernel.config.notes_path))
        except Exception:
            return "", []
    context_parts = []
    used_paths: list[str] = []
    for r in results[:n_files]:
        path = r if isinstance(r, str) else r.get("path", "")
        try:
            content = await self.read(path)
            name = path.replace("\\", "/").split("/")[-1]
            context_parts.append(f"[{name}]\n{content[:n_chars]}")
            used_paths.append(path)
        except Exception:
            continue
    return ("\n\n".join(context_parts) if context_parts else ""), used_paths


async def _build_context(
    self,
    question: str,
    session: dict | None = None,
    *,
    max_files: int | None = None,
    max_chars: int | None = None,
) -> str:
    ctx, _ = await self._collect_context(
        question, session, max_files=max_files, max_chars=max_chars
    )
    return ctx


# ─── Two-phase companion retrieval (dark: feature.companion-twophase.enabled) ───
# Phase 1 = a fast scoped answer; Phase 2 = the concurrent whole-vault answer.
# Reconcile resolves by SOURCE-NOTE RECENCY (not by which retrieval scope) — the
# vault's own dates are the register (.claude/rules/time-dimension.md). Verdicts:
# consistent / prefer_scoped / prefer_full / conflict (surface both, never pick).


def _note_date(self, path: str) -> str:
    """Best 'when authored/updated' date for a vault note, as YYYY-MM-DD ('' if
    none). Resolution order mirrors BaseApp.recall: frontmatter updated → created
    → latest ## Timeline dated bullet → journal filename date → file mtime."""
    p = (path or "").replace("\\", "/")
    if not p:
        return ""
    try:
        props = self.vault_get_properties(p) or {}
        d = props.get("updated") or props.get("created")
        if d:
            return str(d)[:10]
    except Exception:
        pass
    try:
        tl = self.vault_read_section(p, "Timeline") or ""
        dates = []
        for line in tl.splitlines():
            s = line.strip().lstrip("-").strip()
            head = s[:10]
            if len(head) == 10 and head.count("-") == 2 and head[:4].isdigit():
                dates.append(head)
        if dates:
            return max(dates)
    except Exception:
        pass
    # Journal note: the filename IS the date (<journal>/<year>/YYYY-MM-DD.md)
    journal_dir = self.vault_config("journal_dir", "50_Journal").rstrip("/")
    if f"{journal_dir}/" in p:
        stem = p.rsplit("/", 1)[-1].removesuffix(".md")
        if len(stem) == 10 and stem.count("-") == 2 and stem[:4].isdigit():
            return stem
    # No AUTHORED date. Deliberately NOT falling back to file mtime — git/sync
    # resets it, so it gives false recency confidence. "" → the reconcile treats
    # the note as undated and leans to 'conflict' rather than guessing.
    return ""


def _provenance(self, paths: list[str]) -> str:
    """Render a side's source notes as dated labels for the reconcile PROMPT:
    '- project-x.md (2026-06-28)'. Undated notes show '(no date)'."""
    lines = []
    for p in (paths or [])[:6]:
        name = (p or "").replace("\\", "/").rsplit("/", 1)[-1]
        d = self._note_date(p)
        lines.append(f"- {name} ({d or 'no date'})")
    return "\n".join(lines) if lines else "- (no sources)"


def _provenance_items(self, paths: list[str]) -> list[dict]:
    """Structured source attribution for the UI: [{name, date, path}] so the
    conflict/correction cards can render clickable note links (EOS.noteActions)
    alongside each source's date — the 'open the notes to decide' affordance."""
    out = []
    for p in (paths or [])[:6]:
        rel = (p or "").replace("\\", "/")
        out.append({"name": rel.rsplit("/", 1)[-1], "date": self._note_date(p), "path": rel})
    return out


RECONCILE_SYSTEM = (
    "You compare two answers to the user's question, each grounded in vault notes "
    "that carry dates. Decide which is CURRENT by recency — the fact from the more "
    "recently authored/updated source note wins, regardless of which answer it is. "
    "Rules: if the two answers agree (or differ only in non-essential wording) → "
    "'consistent'. If they conflict on a fact AND answer A's source note is clearly "
    "newer → 'prefer_scoped'. If they conflict AND answer B's source note is clearly "
    "newer → 'prefer_full'. If they conflict but you CANNOT tell which source is "
    "newer (missing/equal dates, or the conflicting fact isn't clearly tied to a "
    "dated note) → 'conflict'. If NEITHER side's source note has a date ('no date'), "
    "you cannot determine recency → 'conflict' — do NOT trust an answer that merely "
    "asserts one fact supersedes another without a dated source to back it. Never "
    "assume the whole-vault answer is automatically right; judge only by recency of "
    "the cited sources."
)


async def _scoped_context(
    self, question: str, session: dict | None = None
) -> tuple[str | None, list[str]]:
    """Tier-0+1 scoped retrieval → (formatted context, scoped source paths), or
    (None, []) to signal 'no usable scoped slice — use the whole-vault path'."""
    if self._should_skip_retrieval(question):
        return None, []
    rq = self._retrieval_query(question, session)
    extra = None
    pid = (session or {}).get("project_id") if session else None
    if pid:
        from emptyos.sdk.scoped_retrieval import Scope
        projects_dir = self.vault_config("projects_dir", "10_Projects").rstrip("/")
        extra = [Scope(folder=f"{projects_dir}/{pid}", label=str(pid))]
    try:
        res = await self.scoped_retrieve(
            rq,
            top_k=MAX_CONTEXT_FILES,
            char_cap=MAX_CONTEXT_CHARS,
            router=str(self.app_config("companion.router", "deterministic")),
            max_scopes=int(self.app_config("companion.max_scopes", 3)),
            scoped_max_notes=int(self.app_config("companion.scoped_max_notes", 300)),
            extra_scopes=extra,
        )
    except Exception:
        return None, []
    if res.tier != "scoped":
        return None, []
    from emptyos.sdk.scoped_retrieval import should_escalate

    min_top = float(self.app_config("companion.min_top_score", 0.45))
    hits = [(s, s["score"]) for s in res.snippets]
    if should_escalate(hits, min_top_score=min_top):
        return None, []
    parts = [f"[{s['name']}]\n{s['text']}" for s in res.snippets]
    paths = [s["path"] for s in res.snippets if s.get("path")]
    return ("\n\n".join(parts) if parts else None), paths


VERIFY_INSTRUCTION = (
    "You are producing the AUTHORITATIVE answer from the user's whole vault. Use "
    "ALL the vault snippets provided, not just the first. If two notes disagree, "
    "prefer the most recent or the one that explicitly supersedes/updates/derates "
    "the other, and state that correction plainly. Do not hedge by repeating a "
    "value a later note has superseded."
)


async def _full_answer(
    self, question: str, session: dict | None = None
) -> tuple[str, str, list[str]]:
    """The whole-vault answer (Phase 2). Non-streaming — runs concurrently with the
    Phase-1 stream. Retrieves *more* than the latency-sensitive default and prefers
    superseding facts. Returns (answer_text, context_used, source paths)."""
    nf = int(self.app_config("companion.verify_max_files", 6))
    nc = int(self.app_config("companion.verify_max_chars", 2000))
    ctx, paths = await self._collect_context(
        question, session=session, max_files=nf, max_chars=nc
    )
    system = (await self._build_system(session)) + "\n\n" + VERIFY_INSTRUCTION
    msgs = self._build_chat_messages(session or {}, vault_context=ctx)
    # Guarantee the current question reaches the model even when the session
    # doesn't yet carry it (sessionless HTTP / debug). When the session DOES
    # carry the user turn, _build_chat_messages already injected ctx into it.
    if not msgs or msgs[-1].get("role") != "user":
        user = f"[Vault context — reference]\n{ctx}\n\n{question}" if ctx else question
        msgs = msgs + [{"role": "user", "content": user}]
    try:
        # Grounded-answer task → low-ish temp for a stable authoritative answer.
        text = await self.think(messages=msgs, system=system, domain="text", temperature=0.3)
    except Exception:
        text = ""
    return (text or ""), ctx, paths


async def _reconcile(
    self,
    question: str,
    prelim: str,
    full: str,
    scoped_paths: list[str] | None = None,
    full_paths: list[str] | None = None,
) -> str:
    """Recency-aware verdict between the fast scoped answer (A) and the whole-vault
    answer (B): consistent | prefer_scoped | prefer_full | conflict. Resolves by the
    dates of each side's source notes — the newer source wins, either side; genuine
    ambiguity → 'conflict' (caller surfaces both). Fail-soft (select() never raises).
    Rule-19 note: answers + note NAMES/dates to cloud, never raw note bodies."""
    if not (full or "").strip() or not (prelim or "").strip():
        return "consistent"
    prov_a = self._provenance(scoped_paths or [])
    prov_b = self._provenance(full_paths or [])
    return await self.select(
        f"Question: {question}\n\n"
        f"[Answer A — from these notes]\n{prov_a}\n{prelim[:1500]}\n\n"
        f"[Answer B — from these notes]\n{prov_b}\n{full[:1500]}",
        {
            "consistent": "A and B agree (or differ only in non-essential wording)",
            "prefer_scoped": "they conflict and A's source note is clearly NEWER",
            "prefer_full": "they conflict and B's source note is clearly NEWER",
            "conflict": "they conflict but I cannot tell which source is newer",
        },
        default="consistent",
        system=RECONCILE_SYSTEM,
    )
