"""WebSearch tool — search the web via DuckDuckGo (no API key required).

Uses DuckDuckGo's Lite HTML endpoint so the agent can look up:
  - Documentation, error messages, package versions
  - Current events, dates, facts not in training data
  - Code examples, Stack Overflow answers

Always asks permission (hits public internet). Returns title + URL + snippet
for each result; caller decides which URLs to fetch with the Fetch tool.
"""

from __future__ import annotations

import html
import re

from emptyos.sdk.agent_tools.base import Tool, ToolResult

MAX_RESULTS = 10
DEFAULT_RESULTS = 5
TIMEOUT = 15


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


class WebSearchTool(Tool):
    name = "WebSearch"
    description = (
        "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
        "Use this to look up documentation, error messages, package versions, or "
        "any information not in training data. Then use Fetch to load the full page "
        "if needed. Always asks permission (public internet)."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {
                "type": "integer",
                "description": f"Number of results to return (default {DEFAULT_RESULTS}, max {MAX_RESULTS})",
            },
        },
        "required": ["query"],
    }

    def is_readonly(self, input: dict) -> bool:
        return True  # read-only — safe in plan mode

    def permission_for(self, input: dict) -> str:
        return "ask"

    def permission_summary(self, input: dict) -> str:
        return f"WebSearch: {input.get('query', '')!r}"

    async def run(self, app, **kwargs) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, content="error: query is required")

        n = int(kwargs.get("num_results") or DEFAULT_RESULTS)
        n = max(1, min(n, MAX_RESULTS))

        try:
            import aiohttp
        except ImportError:
            return ToolResult(
                ok=False, content="error: aiohttp is not installed (pip install aiohttp)"
            )

        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; EmptyOS/1.0)",
            "Accept": "text/html",
        }
        params = {"q": query}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        return ToolResult(
                            ok=False, content=f"error: DuckDuckGo returned HTTP {resp.status}"
                        )
                    body = await resp.text()
        except Exception as e:
            return ToolResult(ok=False, content=f"error: search request failed: {e}")

        # Parse result blocks: <div class="result__body"> or <div class="results_links">
        results = []
        # DuckDuckGo HTML Lite: results are in <div class="result ..."> blocks
        # Title in <a class="result__a">, snippet in <a class="result__snippet">
        title_urls = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', body, re.DOTALL
        )
        snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', body, re.DOTALL)

        for i, (href, title_html) in enumerate(title_urls[:n]):
            title = _strip_tags(title_html)
            snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
            # DDG Lite wraps URLs in //duckduckgo.com/l/?uddg=<encoded>
            # Extract the real URL
            real_url = href
            uddg = re.search(r"uddg=([^&]+)", href)
            if uddg:
                from urllib.parse import unquote

                real_url = unquote(uddg.group(1))
            results.append({"title": title, "url": real_url, "snippet": snippet})

        if not results:
            return ToolResult(
                ok=True,
                content=f"No results found for: {query!r}",
                display={"query": query, "results": []},
            )

        lines = [f"Search results for: {query!r}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
            lines.append("")

        return ToolResult(
            ok=True,
            content="\n".join(lines),
            display={"query": query, "results": results},
        )
