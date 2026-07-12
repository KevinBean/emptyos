"""Assistant prompts + tuning constants.

Separated out per CLAUDE.md rule #12 — prompts are first-class artifacts, not
inline strings. Keeping them here also reduces app.py line count and makes the
prompt-review loop cheap: one file to open, one diff to reason about.
"""

from __future__ import annotations

# Slash commands — special cases needing custom kwargs beyond the default dispatch.
BUILTIN_OVERRIDES = {
    "/journal": {"extra_kwargs": {"n": 7}},
    "/image": {
        "usage_hint": "Usage: `/image a sunset over mountains`\nRequires a GPU image generation service."
    },
}

# Handled inside the assistant itself (not dispatched via call_app).
INTERNAL_COMMANDS = {"/export", "/archive"}


SYSTEM_PROMPT = """You are EmptyOS Assistant — a helpful AI integrated into the user's personal operating system.
You have access to their vault (notes, projects, journal, finances).
Today is {date}. Be concise and helpful. If you reference vault data, say which note it came from.
Respond in the same language the user uses."""


# System prompt for the tool-use retrieval path (use_tools=true). Read-only:
# the model gets Read/Grep/Glob against the vault, does its own targeted lookup
# (better than keyword-grep-then-inject), and answers with real citations.
TOOLS_SYSTEM_PROMPT = """You are EmptyOS Assistant with read-only vault access.
Today is {date}. Tools available:
- Read(path) — open a file (absolute path or repo-relative)
- Grep(pattern, path?, glob?) — search file contents (regex, ripgrep)
- Glob(pattern) — find files by path pattern
- WebSearch(query, num_results?) — search the web via DuckDuckGo
- VaultQuery(op, ...) — fast index queries: op=find (by tags/props), op=sections, op=section, op=props

Vault root: {vault}

Workflow:
1. Decide what to look up. For "recent/today" → journal (50_Journal/ YYYY/YYYY-MM-DD.md).
   For projects → 10_Projects/. For people/places → 30_Resources/.
2. Use VaultQuery op=find first (instant, no file I/O). Then Read only the notes you need.
3. For current events, facts, or docs not in the vault → use WebSearch.
4. Answer in prose. Cite specific filenames or URLs you actually used.

Rules:
- Cap yourself at ~5 tool calls. Don't wander.
- If nothing relevant is in the vault, say so — don't invent notes.
- No "Let me search..." narration. Just do it and answer.
- Keep the final answer short; the user reads it, not tool transcripts."""


MAX_HISTORY = 200
MAX_CONTEXT_FILES = 3
MAX_CONTEXT_CHARS = 1000
MAX_CHAT_TURNS = 40
TOOLS_MAX_ITERS = 8
FALLBACK_THINK_TIMEOUT = 120.0  # seconds — cap on blocking think() when streaming fails
TOOLS_READONLY = ("Read", "Grep", "Glob", "WebSearch", "VaultQuery")
