"""Search — vault search with AI-powered answers.

Three modes:
1. search: grep/semantic search across vault files
2. read: read a specific file
3. ask: RAG — search + read top results + think to synthesize answer
"""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.agent_tools.base import Tool, ToolResult

QUERY_EXPAND_SYSTEM = """You are a search query expander. Given a user's search query, generate alternative search terms to improve recall.

## Rules
- The FIRST terms MUST be the query's own keywords, VERBATIM — including any
  non-English characters exactly as written, and each keyword split out on its
  own line (e.g. "交房租 账号" → "交房租", "房租", "账号"). A note written in the
  query's language is found by its original words, not a translation.
- THEN add: English translations (if the query isn't English), synonyms, related concepts.
- Fix typos in the original query.
- Add date-related terms if the query implies time ("last meeting" → also search "2026-04", meeting notes).
- Return 4-8 terms, one per line. Short terms (1-3 words each).

## DO NOT:
- Do NOT return translations ONLY. If the query has non-English words, those
  words MUST appear verbatim in your output, before any translation.
- Add explanations or numbering. Return ONLY the search terms.
- Generate overly broad terms ("information", "notes") that would match everything.
- Repeat the exact same term twice."""

VAULT_RAG_SYSTEM = """You are a knowledge assistant answering questions from a personal note vault (markdown files).

## Rules
- Answer based ONLY on the provided notes. If the notes don't contain the answer, say so clearly.
- Cite sources: mention note titles so the user can find the original.
- Be concise: 2-4 sentences for factual questions, up to a paragraph for analytical questions.
- If multiple notes conflict, acknowledge the discrepancy.

## DO NOT:
- Invent information not in the provided notes.
- Give generic knowledge-base answers when the user is clearly asking about their personal data.
- Start with "Based on your notes..." — they know where the data comes from."""

AGENTIC_VAULT_SYSTEM = """You are a knowledge assistant answering a question from the user's personal markdown note vault.

You have two tools:
- VaultSearch(query): full-text search the vault, returns matching note paths.
- Read(path): read a note's full contents (pass a path VaultSearch returned).

## Workflow
1. Search the vault for the question. Use the user's EXACT terms first — including
   any non-English terms verbatim. Do NOT translate before trying the original.
2. If the first search returns nothing, try synonyms, an English translation, or
   the individual keywords one at a time.
3. Read the most promising notes before answering — don't answer off the path list alone.
4. Answer ONLY from what you actually read. Mention the note(s) you found it in.

## Rules
- The user's data is personal. A query like "交房租 账号" means a specific note exists —
  search for it, never answer from general knowledge.
- Be concise: 2-4 sentences for a factual lookup.
- If, after genuinely searching, you can't find it, say so and suggest terms to try.

## DO NOT
- Answer before searching.
- Invent information that wasn't in a note you read.
- Refuse or pre-translate a non-English query — search it verbatim first."""


class _VaultSearchTool(Tool):
    """Full-text vault search tool for the agentic AI-ask loop.

    Backs onto the search app's own ``_search`` (grep over the vault), so the
    model gets the same recall the RAG path uses — but can iterate: search,
    read, search again with different terms. This is what lets a non-agentic
    provider (ollama, openai-mini) find a note the way claude-cli does natively.
    """

    name = "VaultSearch"
    description = (
        "Full-text search across the user's personal markdown vault. Returns "
        "matching note file paths. Find candidate notes, then Read the most "
        "relevant. Try the user's exact terms first, then synonyms/translations."
    )
    permission = "auto"
    readonly = True
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keywords — the user's exact terms, a translation, or a synonym",
            },
            "top": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["query"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, content="error: query is required")
        top_raw = kwargs.get("top")
        try:
            top = int(top_raw) if top_raw is not None else 10
        except (TypeError, ValueError):
            top = 10
        try:
            paths = await app._search(query, top)
        except Exception as e:
            return ToolResult(ok=False, content=f"search error: {e}")
        if not paths:
            return ToolResult(
                ok=False,
                content=f"No notes matched '{query}'. Try different keywords, a translation, or single terms.",
            )
        listing = "\n".join(f"- {p}" for p in paths)
        return ToolResult(
            ok=True,
            content=f"{len(paths)} notes matched '{query}':\n{listing}",
            display={"query": query, "count": len(paths)},
        )


# Directories that never hold answerable notes.
_EMBED_SKIP_DIRS = {".obsidian", ".trash", "node_modules", "_attachments"}

# Folders whose notes are real but are rarely the answer to a live question.
# Ranked last so a capped candidate set spends its budget on active work.
_EMBED_COLD_PREFIXES = ("40_archive/", "99_attachments/", "70_media/")

# Question scaffolding adds no retrieval value and makes an OR keyword search
# noisy. Keep this deliberately small and English-only: non-English tokens pass
# through unchanged, while exact-phrase search remains the first term for every
# language.
_GREP_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do",
    "does", "for", "from", "had", "has", "have", "how", "i", "in", "into",
    "is", "it", "me", "my", "of", "on", "or", "our", "should", "that",
    "the", "their", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
})
_GREP_MAX_KEYWORDS = 8


def _grep_query_terms(query: str) -> list[str]:
    """Exact phrase first, then distinct meaningful Unicode word tokens.

    Punctuation splits compounds (``cross-bonding`` -> ``cross``, ``bonding``)
    so ordinary prose still recalls a hyphenated query. Single-character ASCII
    tokens are too noisy as fallbacks, but the original query is always kept;
    a deliberate search for ``M`` therefore still works exactly.
    """
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not query:
        return []
    keywords: list[str] = []
    seen = {query.casefold()}
    for token in re.findall(r"[^\W_]+", query, flags=re.UNICODE):
        key = token.casefold()
        if key in seen or key in _GREP_STOP_WORDS:
            continue
        if len(key) == 1 and key.isascii():
            continue
        seen.add(key)
        keywords.append(token)
        if len(keywords) >= _GREP_MAX_KEYWORDS:
            break
    return [query, *keywords]


def _embed_candidate_order(
    rels: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Rank vault notes for a capped embedding candidate set.

    ``rels`` is ``(vault-relative posix path, mtime)``. Active notes first,
    cold archive/media folders last; newest first within each band.

    Exists because the candidate set is capped: on a 12.6k-note vault an
    unranked ``rglob`` walk spent the whole budget before reaching
    ``10_Projects/``. Ranking decides *which* notes the cap keeps.

    mtime is a selection heuristic only, never an authority signal — git and
    sync rewrite it (see ``.claude/rules/scoped-retrieval.md``).
    """
    def _key(item: tuple[str, float]) -> tuple[int, float]:
        rel, mtime = item
        low = rel.lower()
        cold = any(low.startswith(p) for p in _EMBED_COLD_PREFIXES)
        return (1 if cold else 0, -mtime)

    return sorted(rels, key=_key)


class SearchApp(BaseApp):
    # Embedding-based vault search tuning. Used by _embed_search; lifted to
    # class-top so reviewers see them next to the docstring rather than
    # mid-class between methods.
    _MAX_EMBED_NOTES = 5000          # cap candidate set; vault index already filters
    _EMBED_TEXT_LIMIT = 1500         # chars per note fed to embedding (title + head)
    _EMBED_MIN_SCORE = 0.30          # filter visibly-unrelated hits

    def _vault_path(self) -> str:
        return self.kernel.config.get("notes.path", "") or "."

    @cli_command("search", help="Search the vault")
    async def cmd_search(self, query: str = "", mode: str = "search", top: str = "10"):
        if not query:
            self.print_rich("[dim]Usage: eos search <query> [--mode search|ask] [--top N][/dim]")
            return

        n = int(top)
        if mode == "ask":
            answer = await self._ask(query, n)
            print(answer)
        else:
            hits = await self._search_hits(query, n)
            if not hits:
                self.print_rich("[dim]No results found.[/dim]")
                return
            for h in hits:
                count = h.get("matches", 0)
                suffix = f" [dim]· {count} match{'es' if count != 1 else ''}[/dim]" if count else ""
                self.print_rich(f"  {h['path']}{suffix}")
                snippet = h.get("snippet", "")
                if snippet:
                    # Escape [ so wikilinks in note text don't read as Rich markup.
                    self.print_rich(f"    [dim]{snippet.replace('[', chr(92) + '[')}[/dim]")

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        query = request.query_params.get("q", "")
        top = int(request.query_params.get("top", "15"))
        semantic = request.query_params.get("semantic", "").lower() in ("1", "true", "yes")
        mode = (request.query_params.get("mode", "") or "").lower()
        if not query:
            return {"results": [], "query": ""}

        # Embedding-based path: highest-quality semantic recall, no LLM round-trip.
        # Triggered explicitly via mode=embed (or implicitly when semantic=1 and
        # embeddings are available — see _semantic_search fallback chain).
        if mode == "embed":
            try:
                paths, scores, used_method, meta = await self._embed_search(query, top)
                # used_method == "grep" when embeddings are unavailable and we
                # fell through. Surface that honestly so analytics doesn't
                # think every mode=embed request actually hit embeddings.
                self.log_activity(
                    {"action": "search", "query": query, "results": len(paths), "mode": used_method}
                )
                await self.emit("search:query", {"query": query, "results": len(paths), "mode": used_method})
                return {
                    "results": [{"path": p, "score": round(s, 3)} for p, s in zip(paths, scores)],
                    "query": query,
                    "mode": used_method,
                    # Partial coverage must never be silent: the cap decides how
                    # much of the vault embeddings can see at all.
                    "truncated": meta["truncated"],
                    "indexed": meta["indexed"],
                    "total": meta["total"],
                }
            except Exception:
                # Fall through to semantic/grep on any failure
                pass

        # Semantic search with fallback to basic grep on any failure
        if semantic:
            try:
                results, terms = await self._semantic_search(query, top)
                self.log_activity(
                    {
                        "action": "search",
                        "query": query,
                        "results": len(results),
                        "mode": "semantic",
                    }
                )
                await self.emit(
                    "search:query", {"query": query, "results": len(results), "mode": "semantic"}
                )
                return {"results": results, "query": query, "expanded_terms": terms}
            except Exception:
                # Semantic failed — fall through to basic search
                pass

        # Basic grep search (also serves as fallback when semantic fails)
        try:
            results = await self._search(query, top)
        except Exception:
            results = []
        try:
            self.log_activity(
                {"action": "search", "query": query, "results": len(results), "mode": "search"}
            )
        except Exception:
            pass
        try:
            await self.emit("search:query", {"query": query, "results": len(results)})
        except Exception:
            pass
        fallback = semantic  # True if we fell through from a failed semantic search
        resp = {"results": results, "query": query}
        if fallback:
            resp["fallback"] = True
            resp["expanded_terms"] = [query]

        # Supplement with app data sources (bookmarks, quickref)
        app_results = await self._search_app_sources(query)
        if app_results:
            resp["app_results"] = app_results

        return resp

    async def _search_app_sources(self, query: str) -> list[dict]:
        """Search bookmarks and quickref cards for additional results."""
        sources = []
        q = query.lower()

        try:
            bookmarks = await self.call_app("bookmarks", "list_all")
            if isinstance(bookmarks, list):
                for b in bookmarks:
                    text = f"{b.get('title', '')} {b.get('url', '')} {' '.join(b.get('tags', []))} {b.get('summary', '')}".lower()
                    if q in text:
                        sources.append(
                            {
                                "type": "bookmark",
                                "title": b.get("title", ""),
                                "url": b.get("url", ""),
                                "id": b.get("id", ""),
                            }
                        )
        except Exception:
            pass

        try:
            cards = await self.call_app("quickref", "search_cards", query=query)
            if isinstance(cards, list):
                for c in cards:
                    sources.append(
                        {"type": "quickref", "title": c.get("title", ""), "id": c.get("id", "")}
                    )
        except Exception:
            pass

        return sources[:10]

    @web_route("GET", "/api/read")
    async def api_read(self, request):
        path = request.query_params.get("path", "")
        if not path:
            return {"error": "path required"}
        try:
            # Resolve path — handle forward slashes from normalized JS paths
            resolved = Path(path)
            if not resolved.is_absolute():
                vault = self.kernel.config.notes_path
                if vault:
                    resolved = vault / path
            content = await self.read(str(resolved))
            return {"path": path, "content": content}
        except Exception as e:
            return {"error": str(e), "path": path}

    @web_route("GET", "/api/ask")
    async def api_ask(self, request):
        query = request.query_params.get("q", "")
        top = int(request.query_params.get("top", "5"))
        provider = request.query_params.get("provider", "")
        if not query:
            return {"error": "q required"}
        answer, used_provider, latency = await self._ask(query, top, provider=provider)
        await self.emit("search:query", {"query": query, "mode": "ask", "provider": used_provider})

        # Get available providers for retry buttons
        cap = self.kernel.capability("think")
        available = []
        for p in cap.providers:
            try:
                if await p.available() and p.name not in [a["name"] for a in available]:
                    available.append({"name": p.name})
            except Exception:
                pass

        return {
            "answer": answer,
            "query": query,
            "provider": used_provider,
            "latency_ms": latency,
            "available_providers": available,
            "provenance": self.last_provenance(),
        }

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        """Recent search queries from activity log."""
        entries = self.read_activity(limit=30, filter_key="action", filter_val="search")
        seen = set()
        recent = []
        for e in entries:
            q = e.get("query", "")
            if q and q not in seen:
                seen.add(q)
                recent.append({"query": q, "mode": e.get("mode", "search"), "ts": e.get("ts", "")})
        return recent[:15]

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        """Quick search suggestions from recent queries + vault folder names."""
        q = request.query_params.get("q", "").lower()
        suggestions = []

        # Recent queries
        entries = self.read_activity(limit=50, filter_key="action", filter_val="search")
        seen = set()
        for e in entries:
            query = e.get("query", "")
            if query and query.lower() not in seen and (not q or q in query.lower()):
                seen.add(query.lower())
                suggestions.append({"text": query, "source": "recent"})

        # Vault top-level folders
        vault = self.kernel.config.notes_path
        if vault and vault.exists():
            for d in sorted(vault.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    name = d.name
                    if not q or q in name.lower():
                        suggestions.append({"text": name, "source": "folder"})

        return suggestions[:20]

    @web_route("GET", "/api/stats")
    async def api_search_stats(self, request):
        """Search usage statistics."""
        entries = self.read_activity(limit=200, filter_key="action", filter_val="search")
        by_mode = {}
        queries = set()
        for e in entries:
            mode = e.get("mode", "search")
            by_mode[mode] = by_mode.get(mode, 0) + 1
            queries.add(e.get("query", ""))
        return {
            "total_searches": len(entries),
            "unique_queries": len(queries),
            "by_mode": by_mode,
        }

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Strip stray punctuation from inside words (e.g. p'lan → plan)."""
        cleaned = re.sub(r"(?<=\w)[''\"'`](?=\w)", "", query)
        return re.sub(r"\s+", " ", cleaned).strip()

    async def _search(
        self,
        query: str,
        top: int = 15,
        *,
        tokenize: bool = True,
    ) -> list:
        """Search vault notes for matching files. Returns path strings only;
        the snippet/rank layer lives in _search_hits.

        ``tokenize=False`` preserves exact-per-term behavior for the semantic
        expander, which already owns its own multi-term recall and ranking.
        """
        hits = await self._search_hits(query, top, tokenize=tokenize)
        return [h["path"] for h in hits]

    async def _search_hits(
        self,
        query: str,
        top: int = 15,
        *,
        tokenize: bool = True,
    ) -> list[dict]:
        """Grounded vault note search: [{path, snippet, matches}, ...].

        Search the exact phrase plus meaningful query tokens, union the
        candidate files, then inspect their real text. Rank exact-phrase hits
        first, followed by distinct-term coverage, filename coverage, total
        occurrences, and matching-line count. This keeps title/phrase lookups
        strong while making ordinary natural-language questions retrievable;
        repeating one common word cannot outrank a note covering the question.
        Only markdown files survive even when a provider ignores ``glob``.
        """
        import asyncio

        query = self._normalize_query(query)
        if not query:
            return []
        search_terms = _grep_query_terms(query) if tokenize else [query]

        async def _find(term: str):
            try:
                return await self.search(term, path=self._vault_path(), glob="*.md")
            except Exception:
                return []

        batches = await asyncio.gather(*[_find(term) for term in search_terms])
        paths: dict[str, None] = {}
        for results in batches:
            for result in results:
                p = result.get("path", "") if isinstance(result, dict) else str(result)
                # Enforce md-only even when the provider ignored the glob
                # (the plain-grep fallback has no --include wiring).
                if p and p.lower().endswith(".md"):
                    paths.setdefault(p, None)

        keyword_terms = search_terms[1:] if tokenize and len(search_terms) > 1 else [query]
        query_folded = query.casefold()
        keyword_folded = [term.casefold() for term in keyword_terms]
        exact_phrase_is_distinct = len(search_terms) > 1

        def _scan() -> list[dict]:
            ranked: list[tuple[tuple, dict]] = []
            for p in paths:
                try:
                    text = Path(p).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                phrase_hit = False
                matched_terms: set[int] = set()
                occurrence_count = 0
                matching_lines = 0
                snippet = ""
                snippet_rank = (-1, -1, -1)
                for line in text.splitlines():
                    folded = line.casefold()
                    phrase_here = exact_phrase_is_distinct and query_folded in folded
                    line_terms = {
                        index for index, term in enumerate(keyword_folded)
                        if term in folded
                    }
                    if not phrase_here and not line_terms:
                        continue
                    matching_lines += 1
                    if phrase_here:
                        phrase_hit = True
                    matched_terms.update(line_terms)
                    line_occurrences = sum(folded.count(keyword_folded[i]) for i in line_terms)
                    occurrence_count += line_occurrences
                    rank = (int(phrase_here), len(line_terms), line_occurrences)
                    if rank > snippet_rank:
                        snippet_rank = rank
                        snippet = line.strip()

                # Preserve the pre-T14 behavior for regex-shaped provider hits:
                # keep the candidate even if literal verification found no line.
                if not matching_lines:
                    matching_lines = 1
                stem = Path(p).stem.replace("-", " ").replace("_", " ").casefold()
                title_coverage = sum(term in stem for term in keyword_folded)
                hit = {
                    "path": p.replace("\\", "/"),
                    "snippet": snippet[:160],
                    "matches": matching_lines,
                }
                rank_key = (
                    int(phrase_hit),
                    len(matched_terms),
                    title_coverage,
                    occurrence_count,
                    matching_lines,
                )
                ranked.append((rank_key, hit))

            ranked.sort(key=lambda row: (
                -row[0][0], -row[0][1], -row[0][2], -row[0][3], -row[0][4],
                row[1]["path"].casefold(),
            ))
            return [hit for _rank, hit in ranked]

        hits = await asyncio.to_thread(_scan)
        return hits[:top]

    async def voice_search(self, query: str) -> dict:
        """Voice/Aura wrapper over _search — speak the hit count + top note names
        and hand back a card + a link into the full search page with the query
        pre-loaded. The verb registry's `voice` surface dispatches here."""
        query = (query or "").strip()
        if not query:
            return {"say": "What should I search your vault for?"}
        paths = await self._search(query, top=5)
        if not paths:
            return {"say": f"I didn't find any notes matching {query}."}

        def _name(p: str) -> str:
            return p.rsplit("/", 1)[-1][:-3] if p.endswith(".md") else p.rsplit("/", 1)[-1]

        names = [_name(p) for p in paths]
        n = len(paths)
        say = f"Found {n} note{'s' if n != 1 else ''} matching {query}. Top result: {names[0]}."
        from urllib.parse import quote

        return {
            "say": say,
            "card": {"renderer": "task-list", "data": [{"text": nm} for nm in names]},
            "link": {"text": "Open in search", "href": f"/search/?q={quote(query, safe='')}"},
        }

    # Embedding-based vault search ──────────────────────────────────
    # Uses BaseApp.embedding_index() which is backed by emptyos.sdk.embeddings.
    # Note bodies are embedded once (~$0.03 for a 3000-note vault), cached
    # by content hash, queryable in a few ms thereafter.
    # Tuning constants live at class top.

    async def _embed_search(
        self, query: str, top: int = 15
    ) -> tuple[list, list[float], str, dict]:
        """Embedding-cosine vault search. Returns (paths, scores, method, meta)
        where method ∈ {"embed", "grep"} and meta carries the candidate-set
        accounting: {"total", "indexed", "truncated"}.

        Falls back to grep when embeddings aren't available (no API key).
        Caller should surface `method` so observability reflects the actual
        path taken, not the requested one — and surface `truncated` so a
        partially-indexed vault never masquerades as a complete one.

        The candidate set is capped (`_MAX_EMBED_NOTES`, raise via
        `[apps.search] embed_max_notes`). Selection is *ranked* before it is
        capped — see `_embed_candidate_order`. Walking-and-breaking instead
        made `10_Projects/` unreachable on a 12.6k-note vault.
        """
        empty_meta = {"total": 0, "indexed": 0, "truncated": False}
        if not self.embeddings_available:
            paths = await self._search(query, top)
            return paths, [0.0] * len(paths), "grep", empty_meta

        vault = Path(self._vault_path())
        if not vault.exists():
            return [], [], "embed", empty_meta

        # Discover cheaply (stat only), rank, then read just the notes we keep.
        discovered: list[tuple[str, float]] = []
        for p in vault.rglob("*.md"):
            rel = p.relative_to(vault)
            if any(part in _EMBED_SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            # Skip Playwright test fixtures (vault-resident but throwaway)
            if rel.name.startswith("PLAYWRIGHT-TEST-"):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            discovered.append((rel.as_posix(), mtime))

        cap = int(self.app_config("embed_max_notes", self._MAX_EMBED_NOTES) or self._MAX_EMBED_NOTES)
        cap = max(1, cap)  # a 0/negative override must not silently index nothing
        ordered = _embed_candidate_order(discovered)

        candidates: list[dict] = []
        scanned = 0
        for rel, _mtime in ordered:
            if len(candidates) >= cap:
                break
            scanned += 1
            p = vault / rel
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            candidates.append({
                "path": str(p).replace("\\", "/"),
                "text": text[:self._EMBED_TEXT_LIMIT],
            })

        meta = {
            "total": len(ordered),
            "indexed": len(candidates),
            "truncated": len(candidates) >= cap and scanned < len(ordered),
        }
        if not candidates:
            return [], [], "embed", meta

        index = await self.embedding_index(candidates, text_fn=lambda it: it["text"])
        hits = await index.search(query, top_k=top, min_score=self._EMBED_MIN_SCORE)
        paths = [it["path"] for it, _ in hits]
        scores = [s for _, s in hits]
        return paths, scores, "embed", meta

    async def _semantic_search(self, query: str, top: int = 15) -> tuple[list, list]:
        """Semantic search: LLM expands query → multi-term grep → dedup + rank."""
        import asyncio

        query = self._normalize_query(query)
        # Step 1: LLM generates search terms (10s timeout — fast query expansion)
        try:
            raw = await asyncio.wait_for(
                self.think(
                    f"Query: {query}",
                    system=QUERY_EXPAND_SYSTEM,
                    domain="text",
                    temperature=0.2,
                ),
                timeout=10,
            )
            terms = [t.strip().strip("-•*").strip() for t in raw.strip().split("\n") if t.strip()]
            terms = [t for t in terms if t and len(t) < 60][:8]
        except Exception:
            terms = []

        # Deterministic recall floor: ALWAYS search the raw query + its
        # whitespace tokens, ahead of the LLM-expanded terms. Expansion often
        # drops the original-language keywords (esp. non-English — it returns
        # translations only), yet the original words are exactly what match a
        # note written in that language. Without this, a CJK query whose
        # expansion came back all-English finds nothing.
        base = [query] + [t for t in query.split() if len(t) > 1]
        seen_terms = set()
        terms = [
            t for t in base + terms
            if t and not (t in seen_terms or seen_terms.add(t))
        ]
        if not terms:
            terms = [query]

        # Step 2: Search for each term in parallel
        async def search_term(term):
            try:
                return await self._search(term, top, tokenize=False)
            except Exception:
                return []

        all_results = await asyncio.gather(*[search_term(t) for t in terms])

        # Step 3: Deduplicate and rank by frequency (more terms match = higher rank)
        path_count: dict[str, int] = {}
        for results in all_results:
            for path in results:
                path_count[path] = path_count.get(path, 0) + 1

        ranked = sorted(path_count.keys(), key=lambda p: -path_count[p])
        return ranked[:top], terms

    # Agentic AI-ask ────────────────────────────────────────────────
    # A tool-capable provider (ollama, openai-mini) gets VaultSearch + Read
    # tools and iterates like claude-cli does natively — searching the vault
    # itself rather than answering only from the pre-retrieved RAG context.
    # Gated behind the dark flag [apps.search] feature.agentic-ask.enabled.

    def _resolve_tool_capable(self, name: str):
        """Find a ToolCapableProvider by name across the think chain + domain/bucket
        sub-chains. Returns None for unknown names and for NativelyAgenticProviders
        (claude-cli) — those already run their own loop and stay on the RAG path."""
        try:
            from emptyos.capabilities.providers._tool_capable import ToolCapableProvider
        except Exception:
            return None
        for p in self.kernel.capability("think").all_providers():
            if p.name == name and isinstance(p, ToolCapableProvider):
                return p
        return None

    async def _ask_agentic(self, query: str, provider_name: str, context_parts: list) -> str | None:
        """Answer via a bounded read-only tool loop. Returns the answer text, or
        None if the provider isn't tool-capable / unavailable / produced nothing
        (caller then falls back to the pure-RAG path for the same provider).

        Seeded with the semantically-retrieved notes (``context_parts``) because
        VaultSearch is plain grep and can't reliably match multi-word CJK on its
        own — the seed carries recall, the tools let the model dig further."""
        provider = self._resolve_tool_capable(provider_name)
        if provider is None:
            return None
        try:
            if not await provider.available():
                return None
        except Exception:
            return None

        import time as _time

        from emptyos.sdk.agent_loop import AgentSession, run_turn
        from emptyos.sdk.agent_tools.read import ReadTool

        tools = {"VaultSearch": _VaultSearchTool(), "Read": ReadTool()}
        session = AgentSession(
            id=f"search-ask-{int(_time.monotonic() * 1000)}",
            provider_kind=provider.kind,
        )
        seed = ""
        if context_parts:
            seed = "\n\n[Candidate notes already found — read more or search again as needed]\n" + (
                "\n\n".join(context_parts)[:3000]
            )
        try:
            turn = await run_turn(
                session=session,
                user_text=f"{query}{seed}",
                provider=provider,
                tools=tools,
                tool_consent=None,  # read-only tools — nothing to gate
                events=None,
                app_ref=self,
                system=AGENTIC_VAULT_SYSTEM,
                max_iters=6,
                temperature=0.3,
            )
        except Exception:
            return None

        texts = [
            getattr(b, "text", "")
            for b in turn.assistant_blocks
            if getattr(b, "type", "") == "text"
        ]
        answer = "\n".join(t for t in texts if t).strip()
        if not answer:
            return None
        # Mirror _think_with_provider so last_provenance() reflects the agentic call.
        self._last_think_provider = {
            "provider": provider_name,
            "is_cloud": bool(getattr(provider, "is_cloud", False)),
            "model": getattr(provider, "model", None),
            "latency_ms": None,
        }
        return answer

    async def _ask(self, query: str, top: int = 5, provider: str = "") -> tuple[str, str, int]:
        """RAG: search vault, read top results, synthesize answer.

        Returns (answer, provider_name, latency_ms).
        """
        import asyncio
        import time

        # Retrieval — embedding-cosine recall FIRST when available. A natural-
        # language question ("what is the IEC 60287 ampacity master equation")
        # embeds close to the note that answers it; keyword grep + frequency
        # ranking instead buries the specific note under hundreds of broad-
        # keyword matches (a whole-word AND on "60287" never even matches
        # "60287-1-1"), which produced confident false negatives. Fall back to
        # the LLM-expansion + multi-term grep path, which stays LOAD-BEARING:
        # it's the no-embeddings / CJK-keyword recall AND the seed for the
        # agentic loop. Don't make this lazy.
        results = []
        # Scoped-first (Rule-9 2nd consumer of BaseApp.scoped_retrieve, dark):
        # narrow to a folder/tag slice for a faster, more precise context when the
        # query clearly belongs to one. Falls through to whole-vault on any miss,
        # so flag-off (or no usable scope) is byte-identical to the path below.
        if self.app_config("feature.scoped-first.enabled", False) and self.embeddings_available:
            try:
                _sres = await self.scoped_retrieve(query, top_k=top)
                if _sres.usable:
                    results = [s["path"] for s in _sres.snippets]
            except Exception:
                results = []
        if not results and self.embeddings_available:
            try:
                results, _scores, _method, _meta = await self._embed_search(query, top)
            except Exception:
                results = []
        if not results:
            results, _terms = await self._semantic_search(query, top)

        async def _read_safe(path):
            try:
                content = await self.read(path)
                return f"--- {path} ---\n{content[:2000]}"
            except Exception:
                return None

        parts = await asyncio.gather(*[_read_safe(p) for p in results[:top]])
        context_parts = [p for p in parts if p]

        if not context_parts:
            prompt = (
                f"The user asked: {query}\n\n"
                f"No relevant notes were found in the vault. "
                f"If this seems like a personal question about their data, say you couldn't find "
                f"relevant notes and suggest more specific search terms. "
                f"Otherwise, answer briefly from general knowledge."
            )
        else:
            context = "\n\n".join(context_parts)
            prompt = f"Question: {query}\n\nVault notes:\n{context}"

        settings = self.kernel.services.get_optional("settings")
        TIMEOUT = 30
        default_order = "ollama,claude-cli,openai"
        if settings:
            TIMEOUT = int(settings.get("search.ai_timeout", 30) or 30)
            default_order = settings.get("search.ai_providers", default_order) or default_order

        # Provider order: explicit > settings > default
        if provider:
            providers_to_try = [provider]
        else:
            providers_to_try = [p.strip() for p in default_order.split(",") if p.strip()]

        # Agentic AI-ask: tool-capable providers (ollama, openai-mini) get a
        # VaultSearch + Read loop, SEEDED with the retrieved notes, so they can
        # confirm + dig further the way claude-cli does natively.
        agentic = bool(self.app_config("feature.agentic-ask.enabled", False))

        t0 = time.monotonic()
        result = None
        used = "none"

        think_kwargs = {"system": VAULT_RAG_SYSTEM} if context_parts else {}
        for prov in providers_to_try:
            try:
                raw = None
                # Agentic loop first for tool-capable providers; on any miss
                # (not tool-capable, unavailable, empty answer) fall through to
                # the pure-RAG path for the SAME provider.
                if agentic:
                    try:
                        raw = await asyncio.wait_for(
                            self._ask_agentic(query, prov, context_parts),
                            timeout=TIMEOUT * 2,  # the loop does several round-trips
                        )
                    except Exception:
                        raw = None
                if raw is None:
                    raw = await asyncio.wait_for(
                        self._think_with_provider(prov, prompt, "text", think_kwargs),
                        timeout=TIMEOUT,
                    )
                if raw is not None:
                    result = raw
                    used = prov
                    break
            except TimeoutError:
                continue
            except Exception:
                continue

        # Final fallback: default chain (no timeout — last resort)
        if result is None:
            try:
                cap = self.kernel.capability("think")
                raw = await cap.execute(prompt=prompt, domain="text", **think_kwargs)
                result = raw.value
                used = raw.provider
            except Exception as e:
                result = f"All LLM providers failed or timed out. Error: {e}"
                used = "error"

        latency = round((time.monotonic() - t0) * 1000)
        return result, used, latency

    # ── Vault overview (landing page data) ─────────────────

    @web_route("GET", "/api/vault-overview")
    async def api_vault_overview(self, request):
        """Vault stats, recent files, folder breakdown, popular tags for the landing page.

        Uses VaultIndex (in-memory) instead of os.walk for instant response.
        """
        import time as _time

        vi = self.kernel.services.get("vault_index")
        if not vi or not vi._files:
            return {"folders": [], "recent_files": [], "tags": {}, "total_files": 0}

        # Folder breakdown (top-level)
        folders: dict[str, int] = {}
        for entry in vi._files.values():
            top = entry["folder"].split("/")[0] if entry["folder"] else "_root"
            folders[top] = folders.get(top, 0) + 1

        # Recent files (top 12 by mtime from index)
        by_mtime = sorted(vi._files.values(), key=lambda e: -e.get("modified", 0))
        now = _time.time()
        recent = []
        for entry in by_mtime[:12]:
            age = now - entry.get("modified", 0)
            if age < 3600:
                ago = f"{int(age / 60)}m ago"
            elif age < 86400:
                ago = f"{int(age / 3600)}h ago"
            else:
                ago = f"{int(age / 86400)}d ago"
            recent.append(
                {
                    "path": entry["path"],
                    "name": entry["name"],
                    "folder": entry["folder"],
                    "ago": ago,
                }
            )

        # Sort folders by PARA order
        _PARA = self.vault_config(
            "para_folders", "Inbox,Projects,Areas,Resources,Archive,Journal,Attachments"
        )
        _para_names = [f.strip() for f in _PARA.split(",")]
        para_order = {}
        for i, name in enumerate(_para_names):
            for k in folders:
                if name.lower() in k.lower():
                    para_order[k] = i
                    break
        folder_list = [
            {"name": k, "count": v, "order": para_order.get(k, 99)}
            for k, v in folders.items()
            if k != "_root"
        ]
        folder_list.sort(key=lambda x: x["order"])

        # Tags from index — sorted by count descending
        tag_data = vi.tag_counts()
        top_tags = sorted(tag_data.items(), key=lambda x: -x[1])[:20]

        # Search stats
        entries = self.read_activity(limit=200, filter_key="action", filter_val="search")
        total_searches = len(entries)
        unique_queries = len({e.get("query", "") for e in entries})

        return {
            "total_files": vi.file_count(),
            "folders": folder_list,
            "recent_files": recent,
            "tags": [{"name": t, "count": c} for t, c in top_tags],
            "search_stats": {"total": total_searches, "unique": unique_queries},
        }
