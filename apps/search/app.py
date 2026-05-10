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

QUERY_EXPAND_SYSTEM = """You are a search query expander. Given a user's search query, generate alternative search terms to improve recall.

## Rules
- Include: original keywords, English translations (if query is not English), synonyms, related concepts.
- Fix typos in the original query.
- Add date-related terms if the query implies time ("last meeting" → also search "2026-04", meeting notes).
- Return 4-8 terms, one per line. Short terms (1-3 words each).

## DO NOT:
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
            results = await self._search(query, n)
            if not results:
                self.print_rich("[dim]No results found.[/dim]")
                return
            for r in results:
                path = r if isinstance(r, str) else r.get("path", str(r))
                self.print_rich(f"  {path}")

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
                paths, scores, used_method = await self._embed_search(query, top)
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

    async def _search(self, query: str, top: int = 15) -> list:
        """Search vault for matching files."""
        query = self._normalize_query(query)
        results = await self.search(query, path=self._vault_path())
        # Normalize results to list of path strings with forward slashes
        paths = []
        for r in results:
            if isinstance(r, dict):
                paths.append(r.get("path", str(r)).replace("\\", "/"))
            else:
                paths.append(str(r).replace("\\", "/"))
        return paths[:top]

    # Embedding-based vault search ──────────────────────────────────
    # Uses BaseApp.embedding_index() which is backed by emptyos.sdk.embeddings.
    # Note bodies are embedded once (~$0.03 for a 3000-note vault), cached
    # by content hash, queryable in a few ms thereafter.
    # Tuning constants live at class top.

    async def _embed_search(
        self, query: str, top: int = 15
    ) -> tuple[list, list[float], str]:
        """Embedding-cosine vault search. Returns (paths, scores, method)
        where method ∈ {"embed", "grep"}.

        Falls back to grep when embeddings aren't available (no API key).
        Caller should surface `method` so observability reflects the actual
        path taken, not the requested one.
        """
        if not self.embeddings_available:
            paths = await self._search(query, top)
            return paths, [0.0] * len(paths), "grep"

        vault = Path(self._vault_path())
        if not vault.exists():
            return [], [], "embed"

        # Candidate set: every .md under the vault, capped. The vault watcher
        # already dedupes; we just need the file list. Skip noisy locations.
        skip = {".obsidian", ".trash", "node_modules", "_attachments"}
        candidates: list[dict] = []
        for p in vault.rglob("*.md"):
            rel = p.relative_to(vault)
            if any(part in skip or part.startswith(".") for part in rel.parts):
                continue
            # Skip Playwright test fixtures (vault-resident but throwaway)
            if rel.name.startswith("PLAYWRIGHT-TEST-"):
                continue
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
            if len(candidates) >= self._MAX_EMBED_NOTES:
                break

        if not candidates:
            return [], [], "embed"

        index = await self.embedding_index(candidates, text_fn=lambda it: it["text"])
        hits = await index.search(query, top_k=top, min_score=self._EMBED_MIN_SCORE)
        paths = [it["path"] for it, _ in hits]
        scores = [s for _, s in hits]
        return paths, scores, "embed"

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
            terms = [query]

        if not terms:
            terms = [query]

        # Step 2: Search for each term in parallel
        async def search_term(term):
            try:
                return await self._search(term, top)
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

    async def _ask(self, query: str, top: int = 5, provider: str = "") -> tuple[str, str, int]:
        """RAG: search vault, read top results, synthesize answer.

        Returns (answer, provider_name, latency_ms).
        """
        import time

        # Use semantic search for AI Ask — better recall
        results, _terms = await self._semantic_search(query, top)

        # Read top files in parallel, truncate to avoid token overflow
        import asyncio as _aio

        async def _read_safe(path):
            try:
                content = await self.read(path)
                return f"--- {path} ---\n{content[:2000]}"
            except Exception:
                return None

        parts = await _aio.gather(*[_read_safe(p) for p in results[:top]])
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

        # Call LLM with timeout + fallback chain (configurable via settings)
        import asyncio

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

        t0 = time.monotonic()
        result = None
        used = "none"

        think_kwargs = {"system": VAULT_RAG_SYSTEM} if context_parts else {}
        for prov in providers_to_try:
            try:
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
