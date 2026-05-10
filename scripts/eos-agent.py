"""eos-agent — Claude-Code-equivalent shell for EmptyOS, model-agnostic.

Richer version of scripts/test-conversation-mode.py: more tools, CLAUDE.md
auto-loaded into system context, EmptyOS-specific hints the agent needs
(apps/personal/ convention, real BaseApp APIs, anti-hallucination guidance).

Usage:
    # One-shot
    eos-agent exec "Describe what the dashboard app does" --provider openai --model gpt-4o

    # Run the benchmark suite (same cases as test-conversation-mode.py)
    eos-agent eval --provider ollama --model qwen3.5:latest

    # Run a single benchmark test
    eos-agent eval --test 4 --provider openai --model gpt-4o

Design choices vs Goose/Codex:
  - CLAUDE.md + AGENTS.md loaded into system prompt (not user message)
  - Explicit hints about apps/personal/ convention and common gotchas
  - Tools take absolute paths everywhere (no cwd ambiguity)
  - Sandbox writes validated at dispatch time
  - Model-agnostic: openai, anthropic, ollama via separate call_* functions
"""
# Note: do NOT add `from __future__ import annotations` — FastAPI's WebSocket
# route detection depends on runtime type hints, and PEP 563 string annotations
# break the `ws: WebSocket` parameter binding, causing all WebSocket connections
# to be rejected with HTTP 403 before the handler runs. Python 3.10+ supports
# `str | None` and `list[dict]` natively without the __future__ import.

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = REPO_ROOT / "scripts" / "conv-test-cases.toml"
RESULTS_ROOT = REPO_ROOT / "results"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


# ---------- System prompt construction ----------

SYSTEM_PROMPT_HEAD = """You are eos-agent, an AI coding agent operating on EmptyOS.
Your job is to complete the user's task by reading, analyzing, and editing files in the EmptyOS codebase. You have tools to read, write, edit, search, and run shell commands.

EmptyOS is a Python-based "AI-powered operating system". Below is the full CLAUDE.md — the system DNA you must internalize before acting.

=== CLAUDE.md (system DNA) ===
{claude_md}
=== end CLAUDE.md ===
"""

SYSTEM_PROMPT_TAIL = """
=== Operational hints (EmptyOS-specific — do not ignore) ===

1. **apps/personal/ convention**: Some apps live at `apps/<id>/` (community, git-tracked), others at `apps/personal/<id>/` (user's personal apps, gitignored). If a user asks about `apps/healing/` or similar and you can't find it, ALWAYS try `apps/personal/<id>/` before reporting the file missing. Loaders scan both directories — both are first-class.

2. **Real BaseApp vault API** (do NOT hallucinate):
   - `self.vault_create_note(path, frontmatter: dict, body: str)` — 3 args
   - `self.vault_query(tags=[...], **frontmatter_filters)` — in-memory query
   - `self.vault_get_properties(path)` — read frontmatter only
   - `self.vault_update(path, new_props: dict)` — mutate frontmatter
   - `self.vault_read_section(path, name)` / `self.vault_append_section(path, name, text)`
   - `self.vault_read_body(path)` — read prose after frontmatter
   - `self.vault_config(key, default="")` / `self.vault_config_path(key)`
   - `self.write(filename, content)` — plain vault write for simple files
   - There is NO `self.vault_exists`, NO `self.vault.glob`, NO `self.vault_write(title=, content=)`.

3. **Real decorators** (do NOT invent):
   - `@on_event("event:name")` — subscribe to an event (NOT `@event_handler`)
   - `@cli_command("name", help="...")` — register a CLI subcommand
   - `@web_route("GET", "/api/path")` — register an HTTP route
   - `event.data` — access event payload (NOT `event['content']`)

4. **Manifest schema** (apps/*/manifest.toml MUST have):
   - `[app]` with `id`, `name`, `version`, `description`
   - `[app.entry]` with `module` and `class`
   - `[requires]` with `capabilities`, `apps`, `connectors` lists
   - Optional: `[provides.cli]`, `[provides.web]`, `[provides.events]`, `[provides.settings]`, `[provides.assistant]`

5. **Capability use**: Apps NEVER import openai/anthropic directly. Always `await self.think(prompt, system=..., temperature=..., domain=...)`. Prompts should be UPPERCASE module-level constants, with explicit "Do NOT" anti-patterns.

6. **Storage rule**: Human-written/editable data → vault (markdown notes). Machine telemetry (events, logs, billing) → `data/` (SQLite/JSON). See CLAUDE.md "Storage — Two Domains".

7. **When unsure about a function, grep for it** in `emptyos/sdk/base_app.py` before using. Never guess an API signature.

8. **For complex analysis tasks** (e.g., "analyze this file for X"): use the `todo` tool first to list your steps (read file, identify patterns, write analysis). Then mark items complete as you go. This keeps you on track across many tool calls.

9. **When a file-read returns a HINT about apps/personal/**, immediately retry at the suggested path. Don't give up.

10. **Async correctness**: If a method uses `await`, declare it `async def`. If it does NOT call `await` anywhere, do NOT declare it `async`. These are ALL async and must be awaited from async methods: `self.emit(...)`, `self.think(...)`, `self.call_app(...)`, `self.vault_create_note(...)`, `self.vault_append_section(...)`. Putting `await` inside a plain `def` is a SyntaxError at parse time.

11. **Scope discipline — minimal code only**: Match the exact scope requested. If the task says "a method to log X" → write ONE method. If it says "a small app" → minimal manifest + minimal app.py. Do NOT add CLI commands, web routes, helpers, history endpoints, or settings panels unless the task explicitly asks for them. Three similar lines of duplication is better than inventing an abstraction. When in doubt, ship LESS.

12. **Prompt anti-patterns are mandatory**: When you write a prompt for `self.think()`, the system message MUST include at least 3 explicit "Do NOT" lines listing common failure modes (e.g., "Do NOT hedge", "Do NOT invent facts", "Do NOT add a preamble"). A prompt that only says "focus on main points" or "be concise" is "thin" and violates CLAUDE.md rule 12. Thin prompts generate low-quality content.

13. **Verify before writing**: Before emitting code that references a BaseApp method name, a decorator name, or an event name — `grep` to confirm it actually exists. If the task prompt specifies an event like `"journal:entry_created"`, run `grep` for `emit.*journal` in `apps/journal/` to verify that's the real name. **When prompt spec and reality disagree, trust the code.** Cite the line you grepped.

=== Task ===
"""


@functools.lru_cache(maxsize=1)
def build_repo_map() -> str:
    """Compact map of repo structure — borrowed pattern from Aider's repo map.

    Emits one line per app (community + personal) plus SDK/capabilities summary.
    Solves the "agent doesn't know apps/personal/ exists" discovery problem.
    """
    lines = []

    community_apps = []
    personal_apps = []
    apps_dir = REPO_ROOT / "apps"
    if apps_dir.exists():
        for p in sorted(apps_dir.iterdir()):
            if not p.is_dir() or p.name.startswith(("_", ".")):
                continue
            if p.name == "personal":
                continue
            community_apps.append(p.name)
        personal_dir = apps_dir / "personal"
        if personal_dir.exists():
            for p in sorted(personal_dir.iterdir()):
                if p.is_dir() and not p.name.startswith(("_", ".")):
                    personal_apps.append(p.name)

    lines.append(
        f"Community apps ({len(community_apps)}, at `apps/<id>/`): " + ", ".join(community_apps)
    )
    lines.append(
        f"Personal apps ({len(personal_apps)}, at `apps/personal/<id>/`): "
        + ", ".join(personal_apps)
    )

    sdk_dir = REPO_ROOT / "emptyos" / "sdk"
    if sdk_dir.exists():
        sdk_files = [p.name for p in sorted(sdk_dir.rglob("*.py")) if p.name != "__init__.py"]
        lines.append(f"SDK (emptyos/sdk/): {', '.join(sdk_files[:20])}")

    prov_dir = REPO_ROOT / "emptyos" / "capabilities" / "providers"
    if prov_dir.exists():
        providers = [p.stem for p in sorted(prov_dir.glob("*.py")) if p.name != "__init__.py"]
        lines.append(f"Providers (emptyos/capabilities/providers/): {', '.join(providers)}")

    plugins_dir = REPO_ROOT / "plugins"
    if plugins_dir.exists():
        plugins = [
            p.name
            for p in sorted(plugins_dir.iterdir())
            if p.is_dir() and not p.name.startswith(("_", "."))
        ]
        lines.append(f"Plugins: {', '.join(plugins)}")

    return "\n".join(lines)


@functools.lru_cache(maxsize=8)
def _read_claude_md(mtime_ns: int) -> str:
    """Cache the CLAUDE.md body, keyed by mtime so edits invalidate automatically."""
    return CLAUDE_MD.read_text(encoding="utf-8") if CLAUDE_MD.exists() else "(CLAUDE.md not found)"


def build_system_prompt(mode: str = "full") -> str:
    """EmptyOS-context system prompt: full CLAUDE.md + repo map + 13 hints."""
    mtime = CLAUDE_MD.stat().st_mtime_ns if CLAUDE_MD.exists() else 0
    claude_md = _read_claude_md(mtime)
    repo_map = build_repo_map()
    return (
        SYSTEM_PROMPT_HEAD.format(claude_md=claude_md)
        + f"\n=== Repo map (auto-generated) ===\n{repo_map}\n=== end repo map ===\n"
        + SYSTEM_PROMPT_TAIL
        + MODE_PROMPT_ADDENDA.get(mode, "")
    )


# ---------- Generic (non-EmptyOS) context ----------

_EMPTYOS_MARKERS = [
    "emptyos/sdk/base_app.py",
    "apps/personal",
    "scripts/eos-agent.py",
]

_GENERIC_CONVENTION_DOCS = [
    "AGENTS.md",
    "CONVENTIONS.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
]


def detect_context(cwd: Path, override: str | None = None) -> str:
    """Return "eos" or "generic" based on cwd. `override` forces the choice."""
    if override in ("eos", "generic"):
        return override
    cwd = cwd.resolve()
    # Strong EmptyOS signal: cwd inside REPO_ROOT AND has EmptyOS marker files
    if cwd == REPO_ROOT.resolve() or REPO_ROOT.resolve() in cwd.parents:
        # Confirm with at least one marker (defends against a repo named "emptyos" elsewhere)
        if any((REPO_ROOT / m).exists() for m in _EMPTYOS_MARKERS):
            return "eos"
    # Secondary signal: cwd has CLAUDE.md AND EmptyOS markers at its root
    if (cwd / "CLAUDE.md").exists():
        if any((cwd / m).exists() for m in _EMPTYOS_MARKERS):
            return "eos"
    return "generic"


# Per-provider default model — used when /provider is switched without explicit /model
PROVIDER_DEFAULT_MODEL = {
    "openai": "gpt-5.4-mini",  # tool-native, cheap, matches Claude Code on benchmark
    "ollama": "qwen3.5:latest",  # local, zero-cost, strong for chat
    "claude-cli": "",  # Claude picks its own default
    "claude-cli-raw": "",
}


def detect_vault_path(cwd: Path, context: str) -> str:
    """Auto-detect vault path from emptyos.toml [notes.path] when context=eos.

    Returns absolute path string or empty string if not found.
    """
    if context != "eos":
        return ""
    toml_path = REPO_ROOT / "emptyos.toml"
    if not toml_path.exists():
        return ""
    try:
        with toml_path.open("rb") as f:
            cfg = tomllib.load(f)
        path = (cfg.get("notes") or {}).get("path", "")
        if path and Path(path).exists():
            return str(Path(path).resolve())
    except Exception:
        pass
    return ""


def build_vault_system_hint(vault_path: str) -> str:
    """Return a system-prompt addendum describing the vault, or empty string."""
    if not vault_path:
        return ""
    return (
        f"\n\n=== User's vault (auto-detected) ===\n"
        f"The user's personal knowledge vault is mounted at: {vault_path}\n"
        f"Structure follows PARA method:\n"
        f"  {vault_path}/00_Inbox/       — scratch, captures\n"
        f"  {vault_path}/10_Projects/    — active projects\n"
        f"  {vault_path}/20_Areas/       — ongoing responsibilities (Career, Health, Finances)\n"
        f"  {vault_path}/30_Resources/   — reference material (People, Books, Learning)\n"
        f"  {vault_path}/40_Archive/     — completed/inactive\n"
        f"  {vault_path}/50_Journal/     — daily notes at 50_Journal/YYYY/YYYY-MM-DD.md\n"
        f"When the user asks about their schedule/plan/tasks/notes, search the vault directly "
        f"(NOT the EmptyOS repo). For 'today', read the daily note at "
        f"`{vault_path}/50_Journal/{{YEAR}}/{{YYYY-MM-DD}}.md` first.\n"
    )


def find_convention_docs(cwd: Path, max_bytes: int = 64_000) -> list[tuple[str, str]]:
    """Scan cwd + up to 3 parent dirs for AGENTS.md, CONVENTIONS.md, CONTRIBUTING.md, CLAUDE.md.

    Returns list of (filename, content) tuples, deduped by filename, size-capped.
    """
    seen_names: set[str] = set()
    results: list[tuple[str, str]] = []
    total_bytes = 0

    search_dirs = [cwd] + list(cwd.parents)[:3]
    for d in search_dirs:
        for name in _GENERIC_CONVENTION_DOCS:
            if name in seen_names:
                continue
            p = d / name
            if not p.exists():
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if total_bytes + len(content) > max_bytes:
                # Include a truncated copy that fits
                remaining = max_bytes - total_bytes
                if remaining > 500:
                    content = content[:remaining] + "\n\n[... truncated to fit size cap ...]"
                else:
                    continue
            seen_names.add(name)
            results.append((name, content))
            total_bytes += len(content)
            if total_bytes >= max_bytes:
                return results
    return results


GENERIC_SYSTEM_HEAD = """You are eos-agent, a general-purpose coding assistant.

You are operating in directory: {cwd}

You have tools to read, write, edit, search, and run shell commands in this
directory. You have no built-in knowledge of this project's conventions — learn
them from the code you read and any convention docs auto-loaded below.
"""

GENERIC_SYSTEM_TAIL = """

=== Operational rules (generic coding) ===

1. **Read before edit.** Before modifying any file, read it (or the relevant section). Match the existing style (naming, indentation, imports, type annotations) of neighboring code.
2. **Stay in scope.** Make the minimum change the user asked for. Do not add features, tests, or refactors they didn't request.
3. **Grep, don't guess.** If unsure about a function name, import path, or signature, grep for it first. Never invent APIs.
4. **Ask before destructive.** Shell commands that delete, force-push, reset hard, or otherwise cannot be undone should be proposed to the user, not run silently. The approval gate enforces this but you should be thoughtful too.
5. **Absolute paths.** Use absolute paths in all tool calls to avoid cwd ambiguity.
6. **Project docs override these rules.** If the convention docs below contradict anything here, the project documents win.

=== Task ===
"""


def build_generic_system_prompt(mode: str, cwd: Path) -> str:
    """System prompt for non-EmptyOS repos — slim, with auto-loaded convention docs."""
    head = GENERIC_SYSTEM_HEAD.format(cwd=str(cwd).replace("\\", "/"))
    docs = find_convention_docs(cwd)
    doc_section = ""
    if docs:
        doc_section = "\n=== Project conventions (auto-loaded) ===\n"
        for name, content in docs:
            doc_section += f"\n-- {name} --\n{content}\n-- end {name} --\n"
        doc_section += "\n=== end project conventions ===\n"
    else:
        doc_section = "\n(No AGENTS.md, CONVENTIONS.md, CONTRIBUTING.md, or CLAUDE.md found in cwd or parents. Rely on code + user instructions.)\n"
    return head + doc_section + GENERIC_SYSTEM_TAIL + MODE_PROMPT_ADDENDA.get(mode, "")


def build_system_prompt_for_context(
    context: str, mode: str, cwd: Path, vault_path: str | None = None
) -> str:
    """Dispatcher: 'eos' → full CLAUDE.md prompt; 'generic' → slim + convention docs.

    If `vault_path` is passed (or detected from emptyos.toml for eos context),
    append a vault-awareness hint so the agent searches the user's vault not the repo.
    """
    if context == "eos":
        base = build_system_prompt(mode)
    else:
        base = build_generic_system_prompt(mode, cwd)
    if vault_path is None:
        vault_path = detect_vault_path(cwd, context)
    return base + build_vault_system_hint(vault_path or "")


# ---------- Tool definitions ----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the EmptyOS repo or sandbox. Returns full contents (max 200KB).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Path MUST be inside the sandbox dir provided in your task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path inside sandbox"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Search/replace edit in a sandbox file. Use for small edits instead of rewriting the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path inside sandbox"},
                    "find": {
                        "type": "string",
                        "description": "Exact text to find (must be unique in file)",
                    },
                    "replace": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents. Returns file/dir names.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern (e.g., 'apps/**/manifest.toml', '**/*.py'). Relative to repo root unless absolute.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files with ripgrep. Returns matching lines with file:line prefixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search (absolute)"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command (read-only operations like ls, cat, git status, python -c). Writes require write_file/edit_file instead. Max 30s timeout.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Track multi-step work. For complex tasks, list your steps first with action='add' (one call per item), then mark them done with action='complete' as you progress. Lightweight — session-only, not persisted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "complete", "list"]},
                    "item": {
                        "type": "string",
                        "description": "Required for 'add' and 'complete'. For 'complete', match the exact text used in 'add'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


# Session-local todo list (reset per-run)
_SESSION_TODO: list[dict] = []


# Tool subsets per mode (scopes what the model can do)
MODE_TOOLS: dict[str, list[str] | None] = {
    # No tools — pure reasoning/chat. For conceptual Q&A.
    "chat": [],
    # Read-only exploration. Cannot modify anything. For "find all X" tasks.
    "research": ["read_file", "list_dir", "glob", "grep"],
    # Read + structured output only. For analysis tasks that produce one report.
    "analyze": ["read_file", "grep", "glob", "todo", "write_file"],
    # Full code-editing toolkit. For building/refactoring.
    "edit": ["read_file", "write_file", "edit_file", "list_dir", "glob", "grep", "bash", "todo"],
    # Everything (default).
    "full": None,
}


def filtered_tools(mode: str) -> list[dict]:
    """Return the TOOLS list filtered by mode. mode=None or 'full' → all tools."""
    if mode in (None, "full"):
        return TOOLS
    allowed = MODE_TOOLS.get(mode)
    if allowed is None:
        return TOOLS
    allowed_set = set(allowed)
    return [t for t in TOOLS if t["function"]["name"] in allowed_set]


# Mode-specific system prompt addenda
MODE_PROMPT_ADDENDA: dict[str, str] = {
    "chat": (
        "\n\n=== Mode: CHAT ===\n"
        "This is a conceptual question. Answer directly using the EmptyOS architecture context loaded above. "
        "You have NO tools — do not attempt to read files or write anything. "
        "Be accurate, concise, and specific. Cite concepts from CLAUDE.md when relevant. "
        "Do not speculate beyond the architecture context. If you don't know, say so."
    ),
    "research": (
        "\n\n=== Mode: RESEARCH ===\n"
        "This is a read-only investigation. You can read, list, glob, and grep. "
        "You CANNOT write, edit, or execute commands. "
        "Your final response should summarize findings — do not try to save them to a file."
    ),
    "analyze": (
        "\n\n=== Mode: ANALYZE ===\n"
        "Read code, reason about it, produce ONE structured output file. "
        "You can read and grep, but cannot edit existing files or run shell commands."
    ),
    "edit": (
        "\n\n=== Mode: EDIT ===\n"
        "Full code-editing mode. Read the code, then make the minimal changes requested. "
        "Stay within scope — do not add unrequested features."
    ),
    "full": "",
}


# ---------- Tool implementations ----------


def tool_read_file(path: str, sandbox: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        return f"ERROR: path must be absolute, got {path!r}"
    try:
        size = p.stat().st_size
        if size > 200_000:
            return f"ERROR: file too large ({size} bytes), max 200KB — use grep or read in sections"
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # Provide hint for the apps/personal/ convention
        if "/apps/" in path.replace("\\", "/") and "/apps/personal/" not in path.replace("\\", "/"):
            alt = path.replace("/apps/", "/apps/personal/").replace(
                "\\apps\\", "\\apps\\personal\\"
            )
            if Path(alt).exists():
                return (
                    f"ERROR: file not found at {path}\n"
                    f"HINT: found at {alt} — try apps/personal/ for user apps."
                )
        return f"ERROR: file not found: {path}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_write_file(path: str, content: str, sandbox: Path) -> str:
    p = Path(path).resolve()
    try:
        p.relative_to(sandbox.resolve())
    except ValueError:
        return f"ERROR: write_file refused — path {path!r} is outside sandbox {sandbox}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: wrote {len(content)} bytes to {p}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_edit_file(path: str, find: str, replace: str, sandbox: Path) -> str:
    p = Path(path).resolve()
    try:
        p.relative_to(sandbox.resolve())
    except ValueError:
        return "ERROR: edit_file refused — path outside sandbox"
    if not p.exists():
        return f"ERROR: file not found: {path}"
    try:
        original = p.read_text(encoding="utf-8")
        count = original.count(find)
        if count == 0:
            return "ERROR: search text not found in file. Check exact whitespace/casing."
        if count > 1:
            return f"ERROR: search text appears {count} times — must be unique. Provide more context in 'find'."
        updated = original.replace(find, replace, 1)
        p.write_text(updated, encoding="utf-8")
        return f"OK: edited {p} ({len(find)} chars -> {len(replace)} chars)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_list_dir(path: str, sandbox: Path) -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: not found: {path}"
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    try:
        entries = sorted(p.iterdir())
        lines = [f"{'DIR ' if e.is_dir() else 'FILE'} {e.name}" for e in entries[:200]]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_glob(pattern: str, sandbox: Path) -> str:
    base = REPO_ROOT
    # If pattern is absolute, use Path.glob on the parent
    if Path(pattern).is_absolute():
        # Split into base + glob
        parts = Path(pattern).parts
        # Find first component with a wildcard
        for i, part in enumerate(parts):
            if any(c in part for c in "*?["):
                base = Path(*parts[:i])
                pattern = str(Path(*parts[i:]))
                break
    try:
        matches = sorted(str(p) for p in base.glob(pattern))
        if not matches:
            return f"(no matches for {pattern!r} under {base})"
        if len(matches) > 100:
            matches = matches[:100] + [f"... ({len(matches) - 100} more truncated)"]
        return "\n".join(matches)
    except Exception as e:
        return f"ERROR: {e}"


def tool_grep(pattern: str, path: str, sandbox: Path) -> str:
    rg = shutil.which("rg") or shutil.which("ripgrep")
    if not rg:
        return "ERROR: ripgrep not found in PATH"
    try:
        result = subprocess.run(
            [rg, "-n", "--max-count", "20", "--max-columns", "200", pattern, path],
            capture_output=True,
            timeout=15,
        )
        out = result.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return "(no matches)"
        if len(out) > 8000:
            out = out[:8000] + "\n... (truncated)"
        return out
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out"
    except Exception as e:
        return f"ERROR: {e}"


def tool_bash(command: str, sandbox: Path, allow_writes: bool = False) -> str:
    # Basic safety: refuse known-dangerous patterns unless explicit override
    dangerous = [
        r"\brm\s+-rf\s",
        r"\brd\s+/s",
        r"\bdel\s+/s",
        r"\b>\s*[/\\]",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bmkfs",
        r"\bdd\s+if=",
        r"\bgit\s+push",
        r"\bgit\s+reset\s+--hard",
    ]
    for pat in dangerous:
        if re.search(pat, command):
            return f"ERROR: bash refused — pattern matches dangerous operation: {pat!r}"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        out = stdout
        if stderr:
            out += "\n--- stderr ---\n" + stderr
        out = out.strip()
        if len(out) > 8000:
            out = out[:8000] + "\n... (truncated)"
        return (
            f"exit {result.returncode}\n{out}" if out else f"exit {result.returncode} (no output)"
        )
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (30s)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_todo(action: str, item: str) -> str:
    global _SESSION_TODO
    if action == "add":
        if not item:
            return "ERROR: 'item' required for add"
        _SESSION_TODO.append({"item": item, "done": False})
        return f"OK: added todo ({len(_SESSION_TODO)} total)"
    if action == "complete":
        for t in _SESSION_TODO:
            if t["item"] == item and not t["done"]:
                t["done"] = True
                pending = sum(1 for x in _SESSION_TODO if not x["done"])
                return f"OK: completed. {pending} pending."
        return f"ERROR: no matching open todo: {item!r}"
    if action == "list":
        if not _SESSION_TODO:
            return "(no todos)"
        return "\n".join(f"[{'x' if t['done'] else ' '}] {t['item']}" for t in _SESSION_TODO)
    return f"ERROR: unknown action {action!r}"


def dispatch_tool(name: str, args: dict, sandbox: Path) -> str:
    if name == "read_file":
        return tool_read_file(args.get("path", ""), sandbox)
    if name == "write_file":
        return tool_write_file(args.get("path", ""), args.get("content", ""), sandbox)
    if name == "edit_file":
        return tool_edit_file(
            args.get("path", ""), args.get("find", ""), args.get("replace", ""), sandbox
        )
    if name == "list_dir":
        return tool_list_dir(args.get("path", ""), sandbox)
    if name == "glob":
        return tool_glob(args.get("pattern", ""), sandbox)
    if name == "grep":
        return tool_grep(args.get("pattern", ""), args.get("path", ""), sandbox)
    if name == "bash":
        return tool_bash(args.get("command", ""), sandbox)
    if name == "todo":
        return tool_todo(args.get("action", ""), args.get("item", ""))
    return f"ERROR: unknown tool {name!r}"


# ---------- Provider calls (normalized) ----------


def call_ollama(model: str, messages: list[dict], tools: list[dict]) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 65536},
    }
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        response = json.loads(resp.read().decode("utf-8"))

    msg = response.get("message", {})
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        args_raw = tc["function"].get("arguments", {})
        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        tool_calls.append(
            {
                "id": tc.get("id", f"call_ollama_{len(tool_calls)}"),
                "name": tc["function"]["name"],
                "args": args,
            }
        )
    return {
        "content": msg.get("content", "") or "",
        "tool_calls": tool_calls,
        "raw_message": msg,
    }


def call_openai(model: str, messages: list[dict], tools: list[dict]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    clean = []
    for m in messages:
        cm = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        if cm.get("content") is None:
            cm["content"] = ""
        clean.append(cm)

    payload = {
        "model": model,
        "messages": clean,
    }
    if tools:
        payload["tools"] = tools
    # gpt-5 and reasoning models (o1, o3, o4) only support default temperature.
    # Set low temperature for other models.
    if not any(model.startswith(p) for p in ("gpt-5", "o1", "o3", "o4")):
        payload["temperature"] = 0.3
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {e.code}: {body[:500]}") from e

    msg = response["choices"][0]["message"]
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        args_raw = tc["function"].get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            {
                "id": tc.get("id", f"call_openai_{len(tool_calls)}"),
                "name": tc["function"]["name"],
                "args": args,
            }
        )
    return {
        "content": msg.get("content", "") or "",
        "tool_calls": tool_calls,
        "raw_message": msg,
    }


_CLAUDE_MD_BLOCK_RE = re.compile(
    r"=== CLAUDE\.md \(system DNA\) ===.*?=== end CLAUDE\.md ===\n?",
    re.DOTALL,
)


def _strip_claude_md_block(system: str) -> str:
    """Remove the CLAUDE.md block from the system prompt.

    Used by claude-cli (Option A) because the `claude` binary auto-loads CLAUDE.md
    from cwd. Saves ~42KB of duplicated tokens per call (~8-10x reduction in
    system-prompt size). The repo map and operational hints are retained.
    """
    stripped = _CLAUDE_MD_BLOCK_RE.sub(
        "(CLAUDE.md is auto-loaded from cwd — retained below as repo map + hints only.)\n",
        system,
    )
    stripped = stripped.replace(
        "Below is the full CLAUDE.md — the system DNA you must internalize before acting.",
        "You have CLAUDE.md auto-loaded from cwd. Internalize it before acting.",
    )
    return stripped


def call_claude_cli(model: str, messages: list[dict], tools: list[dict]) -> dict:
    """Option A (simple mode): delegate to `claude -p`.

    Claude Code uses its own tools (Read, Grep, Edit, Write, Bash) internally.
    eos-agent's tool loop is short-circuited — one call, return Claude's final text.
    If tools is empty (chat mode), pass --allowedTools "" to disable Claude's tools.

    Optimization: strips the duplicated CLAUDE.md block from the system prompt
    before sending. The `claude` binary auto-loads CLAUDE.md from cwd, so sending
    it via --append-system-prompt would duplicate ~42KB per call. Repo map +
    operational hints (eos-agent's unique value) are retained.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not found in PATH")

    user_msg = ""
    system_parts = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user":
            user_msg = content  # take the last user message
        elif role == "system":
            system_parts.append(content)
    system = "\n\n".join(system_parts)
    # Strip the CLAUDE.md block — the `claude` binary auto-loads it from cwd
    system = _strip_claude_md_block(system)

    cmd = [
        claude_bin,
        "-p",
        user_msg,
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--output-format",
        "text",
    ]
    if model:
        cmd.extend(["--model", model])
    if system:
        cmd.extend(["--append-system-prompt", system[:60_000]])
    if not tools:
        cmd.extend(["--allowedTools", ""])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=900,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timed out (15min)") from None

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {stderr[:500]}")

    return {
        "content": stdout,
        "tool_calls": [],  # Claude handled tools internally
        "raw_message": {"role": "assistant", "content": stdout},
    }


def call_claude_cli_raw(model: str, messages: list[dict], tools: list[dict]) -> dict:
    """Option B (normal mode): Claude as raw LLM behind eos-agent's tool loop.

    Injects the tools as a prompt-engineered JSON schema. Claude emits tool
    calls as ```tool_call``` code blocks; we parse them and return like any other
    provider. Claude's native tools are disabled — eos-agent drives with its own tools.

    Windows argv is capped at ~32KB so we use a SLIM system prompt (no full CLAUDE.md).
    Use Option A (provider=claude-cli) if you want Claude's native CLAUDE.md awareness.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not found in PATH")

    # Slim system prompt — just the essentials (no full CLAUDE.md, no full repo map)
    slim_system = (
        "You are an AI coding agent operating on EmptyOS (a Python-based personal OS). "
        "Key facts:\n"
        "- Repo root: D:/emptyos/\n"
        "- Apps live at apps/<id>/ (community) or apps/personal/<id>/ (user's personal). Try BOTH when a file is missing.\n"
        "- BaseApp real APIs: self.vault_create_note(path, fm, body), self.vault_query(tags=...), "
        "self.think(prompt, system=..., temperature=..., domain=...), self.call_app(app, method, **kwargs), "
        "self.emit(event, payload). All async; methods calling them must be `async def`.\n"
        "- Real decorators: @on_event('event:name'), @cli_command('name'), @web_route('GET', '/path'). "
        "NOT @event_handler. Event payload via event.data (not event['key']).\n"
        "- Apps NEVER import openai/anthropic directly. Always self.think().\n"
        "- Storage rule: human-authored → vault (markdown + frontmatter). Machine telemetry → data/.\n"
    )
    if tools:
        slim_system += "\n=== Available tools ===\n"
        for t in tools:
            fn = t["function"]
            slim_system += f"- **{fn['name']}**: {fn['description']}\n"
            params = fn.get("parameters", {}).get("properties", {})
            for pname, pspec in params.items():
                slim_system += f"    - `{pname}` ({pspec.get('type', 'any')}): {pspec.get('description', '')}\n"
        slim_system += (
            "\n=== How to call tools ===\n"
            "To call a tool, emit EXACTLY this block format on its own lines:\n"
            '```tool_call\n{"name": "tool_name", "args": {"arg1": "value1"}}\n```\n'
            "Only one tool call per response. Wait for the <tool_result> to come back before the next call. "
            "When done, provide your final answer WITHOUT another tool_call block.\n"
        )

    user_msg = ""
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        # Skip eos-agent's system (too big for Windows argv); slim_system replaces it
        if role == "user":
            user_msg = content
        elif role == "tool":
            user_msg += f"\n\n<tool_result>\n{content[:3000]}\n</tool_result>"
        elif role == "assistant":
            prev_content = content if isinstance(content, str) else ""
            if prev_content:
                user_msg += f"\n\n<your_previous_response>\n{prev_content[:1500]}\n</your_previous_response>"
    system = slim_system

    cmd = [
        claude_bin,
        "-p",
        user_msg,
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--output-format",
        "text",
        "--allowedTools",
        "",  # CRITICAL: disable claude's own tools so it uses ours
    ]
    if model:
        cmd.extend(["--model", model])
    if system:
        cmd.extend(["--append-system-prompt", system[:60_000]])

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timed out (5min)") from None

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"claude CLI exit {result.returncode}: {stderr[:500]}")

    # Parse tool_call blocks from the response
    tool_calls = []
    tc_pattern = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)
    for i, match in enumerate(tc_pattern.finditer(stdout)):
        try:
            call = json.loads(match.group(1))
            tool_calls.append(
                {
                    "id": f"call_claude_raw_{i}",
                    "name": call.get("name", ""),
                    "args": call.get("args", {}),
                }
            )
        except json.JSONDecodeError:
            pass

    # Strip tool_call blocks from the content for cleaner display
    content_stripped = tc_pattern.sub("", stdout).strip()

    return {
        "content": content_stripped,
        "tool_calls": tool_calls,
        "raw_message": {"role": "assistant", "content": stdout},
    }


def call_provider(provider: str, model: str, messages: list[dict], tools: list[dict]) -> dict:
    if provider == "ollama":
        return call_ollama(model, messages, tools)
    if provider == "openai":
        return call_openai(model, messages, tools)
    if provider == "claude-cli":
        return call_claude_cli(model, messages, tools)
    if provider == "claude-cli-raw":
        return call_claude_cli_raw(model, messages, tools)
    raise ValueError(f"Unknown provider: {provider}")


# ---------- Agent loop ----------


def _run_turn(
    provider: str,
    model: str,
    messages: list[dict],
    tools_subset: list[dict],
    sandbox: Path,
    approver=None,
    trace: list[dict] | None = None,
    iteration: int = 0,
    mode: str = "full",
) -> dict:
    """Execute ONE model call + all its tool dispatches. Returns turn result.

    Mutates `messages` in place (appends assistant + tool results).
    Mutates `trace` in place if provided.

    Returns:
      {
        "done": bool,            # True if no more tool calls (final answer)
        "content": str,          # model's text content this turn
        "tool_calls": list,      # tool calls that ran (with results)
        "error": str | None,     # if provider call failed
      }

    If `approver` is provided, it's called for each tool call. Returns bool;
    False means deny. Denied calls inject an error tool-result so the model
    can recover.
    """
    if trace is not None:
        trace.append({"step": iteration, "type": "call_model", "mode": mode})

    try:
        response = call_provider(provider, model, messages, tools_subset)
    except Exception as e:
        if trace is not None:
            trace.append({"step": iteration, "type": "error", "error": str(e)})
        return {"done": True, "content": "", "tool_calls": [], "error": str(e)}

    if trace is not None:
        trace.append(
            {
                "step": iteration,
                "type": "model_response",
                "content": response["content"][:500],
                "tool_calls": [
                    {"name": tc["name"], "args": {k: str(v)[:100] for k, v in tc["args"].items()}}
                    for tc in response["tool_calls"]
                ],
            }
        )

    messages.append(response["raw_message"])

    if not response["tool_calls"]:
        return {"done": True, "content": response["content"], "tool_calls": [], "error": None}

    executed = []
    skip_rest = False
    for tc in response["tool_calls"]:
        if skip_rest:
            # user chose "skip rest of turn" on a prior call — synthesize errors for the rest
            result = "ERROR: user skipped remaining tool calls for this turn"
        elif approver is not None:
            try:
                decision = approver.request(tc["name"], tc["args"])
            except KeyboardInterrupt:
                decision = "deny"
            if decision == "deny":
                result = f"ERROR: user denied tool call '{tc['name']}'. Try a different approach or stop."
            elif decision == "skip":
                skip_rest = True
                result = "ERROR: user skipped remaining tool calls for this turn"
            else:  # "allow"
                result = dispatch_tool(tc["name"], tc["args"], sandbox)
        else:
            result = dispatch_tool(tc["name"], tc["args"], sandbox)

        executed.append({"name": tc["name"], "args": tc["args"], "result": result})
        if trace is not None:
            trace.append(
                {
                    "step": iteration,
                    "type": "tool_result",
                    "tool": tc["name"],
                    "result_preview": result[:300],
                    "result_len": len(result),
                }
            )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            }
        )

    return {"done": False, "content": response["content"], "tool_calls": executed, "error": None}


def run_agent(
    provider: str,
    model: str,
    user_prompt: str,
    sandbox: Path,
    max_iterations: int = 20,
    trace_file: Path | None = None,
    mode: str = "full",
) -> dict:
    """Core loop: send messages, execute tool calls, loop until no more tool calls.

    `mode` scopes which tools are available and which system-prompt addendum is used.
    mode="chat" → no tools, pure text response.

    Thin wrapper over `_run_turn()` — builds initial messages then loops turns.
    """
    system = build_system_prompt(mode)
    tools_subset = filtered_tools(mode)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    trace: list[dict] = []
    iteration = 0
    final_text = ""

    # chat mode: single call, no tool loop
    max_loop = 1 if mode == "chat" or not tools_subset else max_iterations

    while iteration < max_loop:
        iteration += 1
        turn = _run_turn(
            provider,
            model,
            messages,
            tools_subset,
            sandbox,
            approver=None,
            trace=trace,
            iteration=iteration,
            mode=mode,
        )
        if turn["error"]:
            break
        if turn["done"]:
            final_text = turn["content"]
            break
        # otherwise loop continues with tool results appended to messages

    if trace_file:
        trace_file.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")

    return {
        "final_text": final_text,
        "iterations": iteration,
        "trace": trace,
        "sandbox_files": [str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file()],
    }


# ---------- Sessions ----------

SESSION_DIR = Path.home() / ".eos-agent" / "sessions"
SESSION_SPOOL_DIR = Path.home() / ".eos-agent" / "spool"
SESSION_SCHEMA_VERSION = 1


def _cwd_hash(cwd: Path) -> str:
    import hashlib

    return hashlib.sha1(str(cwd.resolve()).encode()).hexdigest()[:8]


def _default_session_name(cwd: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{_cwd_hash(cwd)}-{ts}"


class Session:
    """Per-REPL session with config + message history + todos.

    Persisted as JSON at ~/.eos-agent/sessions/<name>.json.
    Atomic save (tmp+rename). Schema versioned for future migrations.
    """

    def __init__(self, data: dict):
        self._data = data

    # ---- factory ----

    @classmethod
    def new(
        cls,
        *,
        cwd: Path,
        sandbox: Path,
        provider: str,
        model: str,
        mode: str,
        context: str,
        name: str | None = None,
        yolo: bool = False,
        max_iterations: int = 20,
        vault: str = "",
    ) -> "Session":
        now = datetime.now().isoformat(timespec="seconds")
        name = name or _default_session_name(cwd)
        # Auto-detect vault from emptyos.toml when context=eos and no explicit vault given
        if not vault:
            vault = detect_vault_path(cwd, context)
        return cls(
            {
                "version": SESSION_SCHEMA_VERSION,
                "name": name,
                "created_at": now,
                "updated_at": now,
                "context": context,
                "cwd": str(cwd),
                "sandbox": str(sandbox),
                "vault": vault,
                "provider": provider,
                "model": model,
                "mode": mode,
                "yolo": yolo,
                "always_allow": [],
                "max_iterations": max_iterations,
                "messages": [],
                "todos": [],
                "repl_meta": {},
            }
        )

    @classmethod
    def load(cls, name: str) -> "Session":
        path = SESSION_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"session not found: {name} (at {path})")
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        version = data.get("version", 0)
        if version > SESSION_SCHEMA_VERSION:
            raise RuntimeError(
                f"session '{name}' has version {version}, but this eos-agent only knows version {SESSION_SCHEMA_VERSION}. "
                f"Upgrade eos-agent or delete the session."
            )
        # Future: run _MIGRATIONS[v] → v+1 in sequence here
        return cls(data)

    # ---- accessors (delegate to dict for simplicity) ----

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        try:
            return self._data[item]
        except KeyError as e:
            raise AttributeError(item) from e

    def set(self, key: str, value) -> None:
        self._data[key] = value

    @property
    def path(self) -> Path:
        return SESSION_DIR / f"{self._data['name']}.json"

    @property
    def data(self) -> dict:
        return self._data

    # ---- persistence ----

    def save(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    # ---- mutation helpers ----

    def append_message(self, message: dict) -> None:
        self._data["messages"].append(message)

    def replace_system_prompt(self, system: str) -> None:
        msgs = self._data["messages"]
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = system
        else:
            msgs.insert(0, {"role": "system", "content": system})

    def clear_messages(self, keep_system: bool = True) -> None:
        msgs = self._data["messages"]
        if keep_system and msgs and msgs[0].get("role") == "system":
            self._data["messages"] = [msgs[0]]
        else:
            self._data["messages"] = []
        self._data["todos"] = []

    def update_sandbox(self, sandbox: Path) -> None:
        self._data["sandbox"] = str(sandbox)

    def rebuild_system_prompt(self) -> None:
        """Re-render the system prompt from current context/mode/cwd/vault and
        install it as messages[0]. Call after mutating any of those fields."""
        prompt = build_system_prompt_for_context(
            self.context,
            self.mode,
            Path(self.cwd),
            vault_path=self.vault,
        )
        self.replace_system_prompt(prompt)


def list_sessions() -> list[dict]:
    """Return lightweight metadata for all saved sessions (no message bodies)."""
    if not SESSION_DIR.exists():
        return []
    out = []
    for p in sorted(SESSION_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        out.append(
            {
                "name": data.get("name", p.stem),
                "updated_at": data.get("updated_at", ""),
                "context": data.get("context", ""),
                "provider": data.get("provider", ""),
                "model": data.get("model", ""),
                "mode": data.get("mode", ""),
                "turns": sum(1 for m in data.get("messages", []) if m.get("role") == "user"),
                "size_kb": p.stat().st_size // 1024,
            }
        )
    return out


# ---------- Modes ----------


def load_cases() -> list[dict]:
    with CASES_FILE.open("rb") as f:
        data = tomllib.load(f)
    return data.get("test", [])


def run_eval(provider: str, model: str, test_filter: str | None = None) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_ROOT / f"conv-test-eos-agent-{provider}-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results dir: {results_dir}")
    print(f"Provider: {provider}, Model: {model}")

    cases = load_cases()
    if test_filter:
        cases = [c for c in cases if c["id"] == test_filter or c["id"].startswith(test_filter)]
        if not cases:
            print(f"No match for {test_filter!r}")
            sys.exit(1)

    global _SESSION_TODO
    summaries = []
    for case in cases:
        case_id = case["id"]
        case_mode = case.get("mode", "full")
        print(f"\n=== Test {case_id} [{case_mode}]: {case['title']} ===")
        _SESSION_TODO = []  # reset per-test
        sandbox = results_dir / case_id / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        prompt = case["prompt"].replace("$SANDBOX", str(sandbox).replace("\\", "/"))

        case_dir = results_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        trace_file = case_dir / "eos_agent_trace.json"

        started = datetime.now()
        try:
            result = run_agent(
                provider, model, prompt, sandbox, trace_file=trace_file, mode=case_mode
            )
            elapsed = (datetime.now() - started).total_seconds()

            summary = {
                "case_id": case_id,
                "title": case["title"],
                "mode": case_mode,
                "provider": provider,
                "model": model,
                "elapsed_sec": round(elapsed, 1),
                "iterations": result["iterations"],
                "final_text": result["final_text"][:4000],
                "sandbox_files": result["sandbox_files"],
                "rubric": case.get("rubric", []),
            }
            (case_dir / "eos_agent_summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print(f"  iterations: {result['iterations']}, elapsed: {elapsed:.1f}s")
            if case_mode == "chat":
                preview = result["final_text"][:150].replace("\n", " ")
                print(f"  chat response: {preview}...")
            else:
                print(f"  files written: {result['sandbox_files']}")
            summaries.append(summary)
        except Exception as e:
            print(f"  ERROR: {e}")
            summaries.append({"case_id": case_id, "error": str(e)})

    (results_dir / "all_summaries.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    print(f"\nAll done. Results in {results_dir}")


def run_exec(
    provider: str, model: str, prompt: str, sandbox_dir: str | None = None, mode: str = "full"
) -> None:
    if sandbox_dir:
        sandbox = Path(sandbox_dir)
        sandbox.mkdir(parents=True, exist_ok=True)
    else:
        sandbox = Path(tempfile.mkdtemp(prefix="eos-agent-exec-"))

    # In chat mode, no sandbox needed — just answer
    if mode == "chat":
        full_prompt = prompt
    else:
        print(f"Sandbox: {sandbox}")
        full_prompt = (
            f"Sandbox directory for any file output: {sandbox}\n"
            f"Only write new files inside that sandbox. Do not modify existing EmptyOS project files.\n\n"
            f"{prompt}"
        )

    started = datetime.now()
    result = run_agent(provider, model, full_prompt, sandbox, mode=mode)
    elapsed = (datetime.now() - started).total_seconds()

    print(f"\n[mode: {mode}] iterations: {result['iterations']}, elapsed: {elapsed:.1f}s")
    if mode != "chat":
        print(f"Files written: {result['sandbox_files']}")
    print(f"\n--- Response ---\n{result['final_text']}")


# ---------- REPL: approval gates ----------

# Tools that mutate filesystem or execute code — require user approval
_DESTRUCTIVE_TOOLS = {"bash", "write_file", "edit_file"}


class Approver:
    """Interactive approval gate for destructive tool calls.

    y=allow once, n=deny, a=always allow this tool for session, s=skip rest of turn.
    If `yolo=True`, auto-approves everything (still logs to console).
    """

    def __init__(
        self, console, sandbox: Path, always_allow: list[str] | None = None, yolo: bool = False
    ):
        self.console = console
        self.sandbox = sandbox
        self.always_allow: set[str] = set(always_allow or [])
        self.yolo = yolo

    def request(self, tool_name: str, args: dict) -> str:
        """Return 'allow', 'deny', or 'skip'."""
        if tool_name not in _DESTRUCTIVE_TOOLS:
            return "allow"
        if self.yolo:
            self.console.print(f"[dim yellow][yolo] auto-approved {tool_name}[/dim yellow]")
            return "allow"
        if tool_name in self.always_allow:
            self.console.print(f"[dim][always-allow] {tool_name}[/dim]")
            return "allow"

        # Build preview panel
        from rich.panel import Panel

        preview = self._render_args_preview(tool_name, args)
        warn = self._sandbox_warning(tool_name, args)
        title = f"Tool request: {tool_name}"
        panel_body = preview
        if warn:
            panel_body = f"[red bold]{warn}[/red bold]\n\n" + panel_body
        self.console.print(Panel(panel_body, title=title, border_style="yellow"))
        try:
            choice = input("Approve? [y/n/a/s] (default n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]denied (interrupted)[/dim]")
            return "deny"
        if choice == "y":
            return "allow"
        if choice == "a":
            self.always_allow.add(tool_name)
            self.console.print(f"[green]Always-allow added: {tool_name}[/green]")
            return "allow"
        if choice == "s":
            return "skip"
        return "deny"

    def _render_args_preview(self, tool_name: str, args: dict) -> str:
        if tool_name == "bash":
            cmd = args.get("command", "")
            return f"command: [cyan]{_truncate_display(cmd, 20)}[/cyan]"
        if tool_name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            return (
                f"path:    [cyan]{path}[/cyan]\n"
                f"content ({len(content)} chars, first 20 lines):\n"
                f"[dim]{_truncate_display(content, 20)}[/dim]"
            )
        if tool_name == "edit_file":
            path = args.get("path", "")
            find = args.get("find", "")
            replace = args.get("replace", "")
            return (
                f"path:    [cyan]{path}[/cyan]\n"
                f"find ({len(find)} chars):\n[red]{_truncate_display(find, 8)}[/red]\n"
                f"replace ({len(replace)} chars):\n[green]{_truncate_display(replace, 8)}[/green]"
            )
        return json.dumps(args, indent=2)[:500]

    def _sandbox_warning(self, tool_name: str, args: dict) -> str | None:
        if tool_name not in ("write_file", "edit_file"):
            return None
        path = args.get("path", "")
        if not path:
            return None
        try:
            p = Path(path).resolve()
            p.relative_to(self.sandbox.resolve())
        except ValueError:
            return f"⚠ WARNING: target path is OUTSIDE sandbox ({self.sandbox})"
        except Exception:
            return None
        return None


def _truncate_display(text: str, max_lines: int) -> str:
    lines = text.splitlines() or [""]
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


# ---------- REPL: slash commands ----------

SLASH_COMMANDS: dict[str, callable] = {}


def slash(name: str, help_text: str = ""):
    """Register a slash command handler."""

    def deco(fn):
        SLASH_COMMANDS[name] = (fn, help_text)
        return fn

    return deco


@slash("help", "Show this help")
def cmd_help(ctx, args: str) -> str:
    from rich.table import Table

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Command")
    t.add_column("Description")
    for name, (_, help_text) in sorted(SLASH_COMMANDS.items()):
        t.add_row(f"/{name}", help_text or "")
    ctx.console.print(t)
    s = ctx.session
    ctx.console.print(
        f"\n[dim]state: context={s.context} provider={s.provider} model={s.model or '<default>'} "
        f"mode={s.mode} yolo={s.yolo} sandbox={s.sandbox}[/dim]"
    )
    return "continue"


@slash("exit", "Exit the REPL")
def cmd_exit(ctx, args: str) -> str:
    return "exit"


@slash("quit", "Exit the REPL (alias)")
def cmd_quit(ctx, args: str) -> str:
    return "exit"


@slash("mode", "Change task mode: chat|research|analyze|edit|full")
def cmd_mode(ctx, args: str) -> str:
    new_mode = (args or "").strip()
    if not new_mode:
        ctx.console.print(f"current mode: {ctx.session.mode}")
        ctx.console.print("choices: chat, research, analyze, edit, full")
        return "continue"
    if new_mode not in MODE_TOOLS:
        ctx.console.print(f"[red]unknown mode: {new_mode}[/red]")
        return "continue"
    ctx.session.set("mode", new_mode)
    ctx.session.rebuild_system_prompt()
    ctx.console.print(f"[green]mode -> {new_mode}[/green]")
    return "continue"


@slash("provider", "Change provider (auto-picks default model unless you override)")
def cmd_provider(ctx, args: str) -> str:
    new_prov = (args or "").strip()
    if not new_prov:
        ctx.console.print(f"current provider: {ctx.session.provider}")
        ctx.console.print("choices: openai, ollama, claude-cli, claude-cli-raw")
        return "continue"
    if new_prov not in ("openai", "ollama", "claude-cli", "claude-cli-raw"):
        ctx.console.print(f"[red]unknown provider: {new_prov}[/red]")
        return "continue"
    ctx.session.set("provider", new_prov)
    # Auto-set a sensible default model for the new provider
    default_model = PROVIDER_DEFAULT_MODEL.get(new_prov, "")
    ctx.session.set("model", default_model)
    ctx.console.print(f"[green]provider -> {new_prov}[/green]")
    ctx.console.print(
        f"[dim]model -> {default_model or '<claude default>'}  (use /model to override)[/dim]"
    )
    if new_prov == "claude-cli":
        ctx.console.print(
            "[yellow]note: claude-cli runs tools internally — approval gate disabled for this provider[/yellow]"
        )
    return "continue"


@slash("model", "Change model for current provider")
def cmd_model(ctx, args: str) -> str:
    new_model = (args or "").strip()
    if not new_model:
        ctx.console.print(f"current model: {ctx.session.model or '<default>'}")
        return "continue"
    ctx.session.set("model", new_model)
    ctx.console.print(f"[green]model -> {new_model}[/green]")
    return "continue"


@slash("reset", "Clear conversation (keep config)")
def cmd_reset(ctx, args: str) -> str:
    ctx.session.clear_messages(keep_system=True)
    global _SESSION_TODO
    _SESSION_TODO = []
    ctx.console.print("[green]conversation cleared[/green]")
    return "continue"


@slash("save", "Save session: /save [name]")
def cmd_save(ctx, args: str) -> str:
    name = (args or "").strip()
    if name:
        ctx.session.set("name", name)
    ctx.session.save()
    ctx.console.print(f"[green]saved: {ctx.session.path}[/green]")
    return "continue"


@slash("load", "Load a saved session: /load <name>")
def cmd_load(ctx, args: str) -> str:
    name = (args or "").strip()
    if not name:
        ctx.console.print("[red]usage: /load <name>[/red]")
        return "continue"
    try:
        new_session = Session.load(name)
    except Exception as e:
        ctx.console.print(f"[red]load failed: {e}[/red]")
        return "continue"
    ctx.session = new_session
    global _SESSION_TODO
    _SESSION_TODO = list(new_session.todos)
    ctx.console.print(f"[green]loaded: {name} ({len(new_session.messages)} messages)[/green]")
    return "continue"


@slash("sessions", "List saved sessions")
def cmd_sessions(ctx, args: str) -> str:
    from rich.table import Table

    sessions = list_sessions()
    if not sessions:
        ctx.console.print("[dim]no saved sessions[/dim]")
        return "continue"
    t = Table(show_header=True, header_style="bold cyan")
    for col in ("name", "updated_at", "context", "provider", "model", "mode", "turns", "size_kb"):
        t.add_column(col)
    for s in sessions:
        t.add_row(
            *(
                str(s[k])
                for k in (
                    "name",
                    "updated_at",
                    "context",
                    "provider",
                    "model",
                    "mode",
                    "turns",
                    "size_kb",
                )
            )
        )
    ctx.console.print(t)
    return "continue"


@slash("tools", "List tools available in current mode")
def cmd_tools(ctx, args: str) -> str:
    tools_list = filtered_tools(ctx.session.mode)
    if not tools_list:
        ctx.console.print(f"[dim]mode '{ctx.session.mode}' has no tools[/dim]")
        return "continue"
    from rich.table import Table

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Tool")
    t.add_column("Description")
    for tool in tools_list:
        fn = tool["function"]
        t.add_row(fn["name"], fn["description"][:80])
    ctx.console.print(t)
    return "continue"


@slash("context", "Switch context: auto|eos|generic")
def cmd_context(ctx, args: str) -> str:
    new_ctx = (args or "auto").strip()
    if new_ctx not in ("auto", "eos", "generic"):
        ctx.console.print("[red]choices: auto, eos, generic[/red]")
        return "continue"
    if new_ctx == "auto":
        resolved = detect_context(Path(ctx.session.cwd), None)
    else:
        resolved = new_ctx
    ctx.session.set("context", resolved)
    ctx.session.rebuild_system_prompt()
    ctx.console.print(f"[green]context -> {resolved}[/green]")
    return "continue"


@slash("vault", "Set or show user's vault path: /vault <path> | /vault off")
def cmd_vault(ctx, args: str) -> str:
    arg = (args or "").strip()
    if not arg:
        current = ctx.session.vault or "(not set)"
        ctx.console.print(f"current vault: {current}")
        ctx.console.print(
            "[dim]Usage: /vault <path>  (agent will search it for daily notes, projects, etc)[/dim]"
        )
        ctx.console.print("[dim]       /vault off   (clear vault awareness)[/dim]")
        return "continue"
    if arg.lower() == "off":
        ctx.session.set("vault", "")
    else:
        p = Path(arg).resolve()
        if not p.exists():
            ctx.console.print(f"[red]path does not exist: {p}[/red]")
            return "continue"
        ctx.session.set("vault", str(p))
    ctx.session.rebuild_system_prompt()
    ctx.console.print(f"[green]vault -> {ctx.session.vault or '(cleared)'}[/green]")
    return "continue"


@slash("sandbox", "Set sandbox directory: /sandbox <path>")
def cmd_sandbox(ctx, args: str) -> str:
    path_str = (args or "").strip()
    if not path_str:
        ctx.console.print(f"current sandbox: {ctx.session.sandbox}")
        return "continue"
    p = Path(path_str).resolve()
    if not p.exists():
        try:
            ok = input(f"Path {p} does not exist. Create? [y/N]: ").strip().lower() == "y"
        except (KeyboardInterrupt, EOFError):
            ok = False
        if not ok:
            return "continue"
        p.mkdir(parents=True, exist_ok=True)
    ctx.session.update_sandbox(p)
    ctx.approver.sandbox = p
    ctx.console.print(f"[green]sandbox -> {p}[/green]")
    return "continue"


@slash("yolo", "Toggle --yolo (skip approval prompts): /yolo on|off")
def cmd_yolo(ctx, args: str) -> str:
    arg = (args or "").strip().lower()
    if arg == "on":
        ctx.session.set("yolo", True)
        ctx.approver.yolo = True
    elif arg == "off":
        ctx.session.set("yolo", False)
        ctx.approver.yolo = False
    else:
        ctx.session.set("yolo", not ctx.session.yolo)
        ctx.approver.yolo = ctx.session.yolo
    ctx.console.print(f"[yellow]yolo: {'ON' if ctx.session.yolo else 'off'}[/yellow]")
    return "continue"


@slash("retry", "Re-run the last user message")
def cmd_retry(ctx, args: str) -> str:
    msgs = ctx.session.messages
    last_user = None
    for m in reversed(msgs):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break
    if not last_user:
        ctx.console.print("[dim]no prior user message to retry[/dim]")
        return "continue"
    # remove messages after last user
    last_idx = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
    ctx.session.data["messages"] = msgs[:last_idx]  # drop last user too, will re-add
    return f"retry:{last_user}"


# ---------- REPL: main loop ----------


def _read_multiline_input(prompt: str = "> ") -> str | None:
    """Read input. Blank line OR EOF terminates multi-line. Returns None on immediate EOF.

    `/cmd` prefix: single-line, no multi-line collection.
    """
    try:
        first = input(prompt)
    except EOFError:
        return None
    if first.startswith("/") or not first.strip():
        return first
    # Multi-line: keep reading until blank line
    lines = [first]
    while True:
        try:
            more = input("... ")
        except (EOFError, KeyboardInterrupt):
            break
        if not more.strip():
            break
        lines.append(more)
    return "\n".join(lines)


class ReplCtx:
    def __init__(self, session: "Session", console, approver: Approver):
        self.session = session
        self.console = console
        self.approver = approver
        self.running = True


def _handle_slash(ctx: ReplCtx, line: str) -> str:
    """Return 'continue', 'exit', or 'retry:<text>'."""
    parts = line[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    entry = SLASH_COMMANDS.get(cmd)
    if not entry:
        # Find similar commands (simple prefix match)
        similar = [k for k in SLASH_COMMANDS if k.startswith(cmd[:2])]
        ctx.console.print(f"[red]unknown command: /{cmd}[/red]")
        if similar:
            ctx.console.print(f"[dim]did you mean: {', '.join(f'/{s}' for s in similar[:5])}[/dim]")
        ctx.console.print("[dim]type /help for all commands[/dim]")
        return "continue"
    fn, _ = entry
    return fn(ctx, args)


def _execute_user_turn(ctx: ReplCtx, user_text: str) -> None:
    """Append user message, run agent turns until done, render response."""
    session = ctx.session
    # Ensure system prompt exists
    if not session.messages or session.messages[0].get("role") != "system":
        session.rebuild_system_prompt()

    session.append_message({"role": "user", "content": user_text})

    tools_subset = filtered_tools(session.mode)
    sandbox = Path(session.sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)

    # Sync _SESSION_TODO with session
    global _SESSION_TODO
    _SESSION_TODO = list(session.todos)

    max_loop = 1 if session.mode == "chat" or not tools_subset else session.max_iterations
    iteration = 0
    final_text = ""
    while iteration < max_loop:
        iteration += 1
        with ctx.console.status("[cyan]thinking...[/cyan]", spinner="dots"):
            turn = _run_turn(
                session.provider,
                session.model,
                session.messages,
                tools_subset,
                sandbox,
                approver=ctx.approver,
                trace=None,
                iteration=iteration,
                mode=session.mode,
            )
        # Show tool calls that ran this iteration
        for tc in turn["tool_calls"]:
            ctx.console.print(
                f"[dim magenta]-> {tc['name']}({_fmt_tool_args(tc['args'])})[/dim magenta]"
            )
        if turn["error"]:
            ctx.console.print(f"[red]error: {turn['error']}[/red]")
            break
        if turn["done"]:
            final_text = turn["content"]
            break

    # Sync todos back from global
    session.set("todos", list(_SESSION_TODO))

    # Render final response
    if final_text:
        from rich.markdown import Markdown

        ctx.console.print(Markdown(final_text))
    ctx.console.print()  # trailing blank line

    # Autosave after each turn
    try:
        session.save()
    except Exception as e:
        ctx.console.print(f"[dim red]autosave failed: {e}[/dim red]")


def _fmt_tool_args(args: dict) -> str:
    """Single-line summary of tool args for trace display."""
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 50:
            s = s[:50] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def repl_main(args) -> None:
    from rich.console import Console

    # legacy_windows=False bypasses the cp1252-limited console renderer;
    # Rich falls back to writing ANSI directly, which our stdout handles
    # (Windows Terminal + modern cmd.exe both support ANSI). When stdout is
    # piped (not a tty), Rich uses a plain writer that tolerates UTF-8.
    try:
        console = Console(legacy_windows=False)
    except TypeError:
        console = Console()

    # Resolve config
    cwd = Path.cwd()
    context = detect_context(cwd, args.context if args.context != "auto" else None)

    if args.resume:
        try:
            session = Session.load(args.resume)
        except Exception as e:
            console.print(f"[red]failed to resume '{args.resume}': {e}[/red]")
            sys.exit(1)
        # Allow CLI flags to EXPLICITLY override loaded values.
        # (--mode default=None, so if the user didn't pass it, session's value wins.)
        if args.provider is not None:
            session.set("provider", args.provider)
        if args.model is not None:
            session.set("model", args.model)
        if args.mode is not None:
            session.set("mode", args.mode)
        if args.yolo:
            session.set("yolo", True)
        sandbox = Path(session.sandbox)
    else:
        # Pick sandbox default
        if args.sandbox:
            sandbox = Path(args.sandbox).resolve()
        elif context == "eos" and cwd.resolve() == REPO_ROOT.resolve():
            sandbox = REPO_ROOT / "_sandbox" / f"repl-{_cwd_hash(cwd)}"
            console.print(
                f"[yellow]Sandbox defaulted to [bold]{sandbox}[/bold] (don't want to clobber EmptyOS source). "
                f"Use /sandbox <path> or --sandbox to change.[/yellow]"
            )
        else:
            sandbox = cwd
        sandbox.mkdir(parents=True, exist_ok=True)

        provider = args.provider or ("openai" if os.environ.get("OPENAI_API_KEY") else "ollama")
        model = args.model or PROVIDER_DEFAULT_MODEL.get(provider, "")
        mode = args.mode or "full"
        session = Session.new(
            cwd=cwd,
            sandbox=sandbox,
            provider=provider,
            model=model,
            mode=mode,
            context=context,
            name=args.name,
            yolo=args.yolo,
            max_iterations=args.max_iterations,
        )
        session.rebuild_system_prompt()

    approver = Approver(
        console=console,
        sandbox=Path(session.sandbox),
        always_allow=list(session.always_allow),
        yolo=session.yolo,
    )
    ctx = ReplCtx(session=session, console=console, approver=approver)

    # Banner
    console.print(f"[bold cyan]eos-agent REPL[/bold cyan]  [dim]session: {session.name}[/dim]")
    console.print(
        f"[dim]context={session.context} provider={session.provider} "
        f"model={session.model or '<default>'} mode={session.mode} "
        f"yolo={'ON' if session.yolo else 'off'}[/dim]"
    )
    if session.provider == "claude-cli":
        console.print(
            "[yellow]note: claude-cli runs its own tools — approval gate disabled[/yellow]"
        )
    console.print(f"[dim]sandbox: {session.sandbox}[/dim]")
    if session.vault:
        console.print(
            f"[dim]vault:   {session.vault}  (agent will search here for notes/schedule/projects)[/dim]"
        )
    console.print(
        "[dim]Type your message (blank line to send multi-line). /help for commands. /exit to quit.[/dim]\n"
    )

    # Main loop
    while ctx.running:
        try:
            line = _read_multiline_input("> ")
        except KeyboardInterrupt:
            console.print("\n[dim](Ctrl-C — use /exit to quit, or type a message)[/dim]")
            continue
        if line is None:
            console.print("\n[dim]EOF — exiting[/dim]")
            break
        line = line.strip("\n")
        if not line.strip():
            continue
        if line.startswith("/"):
            action = _handle_slash(ctx, line)
            if action == "exit":
                break
            if action.startswith("retry:"):
                line = action[len("retry:") :]
                # fall through to execute
            else:
                continue

        # Execute user turn
        try:
            # Sync approver state from session (in case /yolo or /always_allow changed)
            approver.yolo = session.yolo
            session.set("always_allow", sorted(approver.always_allow))
            _execute_user_turn(ctx, line)
        except KeyboardInterrupt:
            console.print("\n[yellow]turn cancelled[/yellow]")
            # Append a note to history so model knows
            session.append_message(
                {
                    "role": "user",
                    "content": "(user pressed Ctrl-C — previous request cancelled)",
                }
            )
            try:
                session.save()
            except Exception:
                pass
        except Exception as e:
            console.print(f"[red]turn error: {e}[/red]")

    # Final save
    try:
        session.save()
        console.print(f"[dim]session saved: {session.path}[/dim]")
    except Exception as e:
        console.print(f"[red]final save failed: {e}[/red]")


# ---------- Web CLI: FastAPI + WebSocket server ----------

WEB_CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>eos-agent</title>
<style>
:root {
  --bg: #0a0a0a; --fg: #e6e6e6; --dim: #888; --accent: #7ac7ff;
  --user-bg: #1a3a5c; --agent-bg: #1a1a1a; --tool-bg: #2a1a2a;
  --err: #ff7a7a; --warn: #ffc76a; --ok: #7affc4;
  /* Mode colors (risk-coded) */
  --mode-chat: #4a90e2;       /* blue — read-only, no tools */
  --mode-research: #5cb85c;   /* green — read-only */
  --mode-analyze: #f0ad4e;    /* yellow — writes one file */
  --mode-edit: #e67e22;       /* orange — full write + edit */
  --mode-full: #e74c3c;       /* red — everything incl. bash */
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
body { display: flex; flex-direction: column; }
header { padding: 10px 14px; background: #111; border-bottom: 1px solid #222;
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; flex-wrap: wrap; }
header strong { color: var(--accent); font-size: 15px; }
#meta { font-size: 11px; color: var(--dim); flex: 1; text-align: right; min-width: 140px; }
.mode-pill { display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 12px; border-radius: 999px; font-size: 13px; font-weight: bold;
  background: var(--mode-chat); color: #000; letter-spacing: 0.5px; text-transform: uppercase; }
.mode-pill.mode-chat     { background: var(--mode-chat); }
.mode-pill.mode-research { background: var(--mode-research); }
.mode-pill.mode-analyze  { background: var(--mode-analyze); }
.mode-pill.mode-edit     { background: var(--mode-edit); }
.mode-pill.mode-full     { background: var(--mode-full); color: #fff; }
.mode-hint { font-size: 10px; opacity: 0.8; font-weight: normal; text-transform: none; letter-spacing: 0; }
body[data-mode] footer { border-top-width: 2px; }
body[data-mode="chat"] footer     { border-top-color: var(--mode-chat); }
body[data-mode="research"] footer { border-top-color: var(--mode-research); }
body[data-mode="analyze"] footer  { border-top-color: var(--mode-analyze); }
body[data-mode="edit"] footer     { border-top-color: var(--mode-edit); }
body[data-mode="full"] footer     { border-top-color: var(--mode-full); }
body[data-mode="chat"] #input:focus     { border-color: var(--mode-chat); }
body[data-mode="research"] #input:focus { border-color: var(--mode-research); }
body[data-mode="analyze"] #input:focus  { border-color: var(--mode-analyze); }
body[data-mode="edit"] #input:focus     { border-color: var(--mode-edit); }
body[data-mode="full"] #input:focus     { border-color: var(--mode-full); }
#log { flex: 1; overflow-y: auto; padding: 12px;
  display: flex; flex-direction: column; gap: 8px; }
.msg { padding: 8px 12px; border-radius: 8px; max-width: 92%;
  white-space: pre-wrap; word-wrap: break-word; }
.msg.user { align-self: flex-end; background: var(--user-bg); }
.msg.agent { align-self: flex-start; background: var(--agent-bg); }
.msg.tool { align-self: flex-start; background: var(--tool-bg);
  font-size: 12px; color: var(--dim); max-width: 100%; }
.msg.err { color: var(--err); }
.msg.warn { color: var(--warn); }
.msg.ok { color: var(--ok); }
.msg h1, .msg h2, .msg h3 { margin: 6px 0; font-size: 1em; color: var(--accent); }
.msg code { background: #000; padding: 1px 4px; border-radius: 3px; }
.msg pre { background: #000; padding: 8px; border-radius: 4px;
  overflow-x: auto; margin: 4px 0; }
.msg pre code { background: transparent; padding: 0; }
.msg a { color: var(--accent); text-decoration: underline;
  word-break: break-all; }
.msg a:hover { background: rgba(122,199,255,0.15); }
.msg ul { margin: 4px 0 4px 20px; padding: 0; }
.msg li { margin: 2px 0; }
footer { position: relative; padding: 8px; border-top: 1px solid #222; background: #111;
  display: flex; gap: 8px; align-items: flex-end; }
#input { flex: 1; background: #0a0a0a; color: var(--fg); border: 1px solid #333;
  border-radius: 4px; padding: 8px; font: inherit; resize: none;
  min-height: 40px; max-height: 200px; }
#input:focus { outline: none; border-color: var(--accent); }
button { background: var(--accent); color: #000; border: none; border-radius: 4px;
  padding: 10px 16px; font-weight: bold; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: not-allowed; }

/* Slash command picker */
#picker { position: absolute; bottom: 100%; left: 8px; right: 8px;
  background: #1a1a1a; border: 1px solid #333; border-bottom: none;
  border-radius: 6px 6px 0 0; max-height: 280px; overflow-y: auto;
  display: none; box-shadow: 0 -4px 12px rgba(0,0,0,0.5); }
#picker.open { display: block; }
.pick-item { padding: 8px 12px; cursor: pointer; display: flex; gap: 12px;
  align-items: baseline; border-bottom: 1px solid #222; }
.pick-item:last-child { border-bottom: none; }
.pick-item.active { background: #2a3a4c; }
.pick-item:hover { background: #1f2a3a; }
.pick-cmd { color: var(--accent); font-weight: bold; min-width: 110px; }
.pick-desc { color: var(--dim); font-size: 12px; flex: 1; }
.pick-args { color: #f0ad4e; font-size: 11px; font-style: italic; }
.pick-hint { padding: 6px 12px; font-size: 10px; color: var(--dim);
  background: #0a0a0a; border-top: 1px solid #222; }
#modal { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none;
  justify-content: center; align-items: center; padding: 20px; z-index: 100; }
#modal.open { display: flex; }
#modal-inner { background: #1a1a1a; padding: 16px; border-radius: 8px;
  max-width: 600px; width: 100%; border: 1px solid #444; }
#modal-inner h3 { color: var(--warn); margin-bottom: 8px; }
#modal pre { background: #000; padding: 8px; border-radius: 4px;
  overflow-x: auto; max-height: 300px; margin: 8px 0; }
.btn-row { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.btn-row button { flex: 1; min-width: 80px; }
.btn-deny { background: #444; color: #fff; }
.btn-always { background: #7affc4; color: #000; }
.btn-skip { background: #ffc76a; color: #000; }
@media (max-width: 600px) {
  body { font-size: 14px; }
  #log { padding: 8px; gap: 6px; }
  .msg { padding: 6px 10px; max-width: 95%; }
  #input { min-height: 50px; }
  button { padding: 12px 16px; }
}
</style>
</head>
<body>
<header>
  <strong>eos-agent</strong>
  <span id="mode-pill" class="mode-pill mode-chat">CHAT <span class="mode-hint">no tools</span></span>
  <span id="meta">connecting...</span>
</header>
<div id="log"></div>
<footer>
  <div id="picker"></div>
  <textarea id="input" placeholder="Type a message. Try /help or just /  for commands. Shift+Enter for newline." rows="1"></textarea>
  <button id="send">Send</button>
</footer>
<div id="modal">
  <div id="modal-inner">
    <h3 id="modal-title">Tool approval</h3>
    <div id="modal-body"></div>
    <div class="btn-row">
      <button class="btn-approve" data-d="allow">Allow</button>
      <button class="btn-deny" data-d="deny">Deny</button>
      <button class="btn-always" data-d="always">Always</button>
      <button class="btn-skip" data-d="skip">Skip rest</button>
    </div>
  </div>
</div>
<script>
const qs = new URLSearchParams(location.search);
const token = qs.get("t") || "";
const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
const wsUrl = `${wsProto}//${location.host}/ws${token ? `?t=${encodeURIComponent(token)}` : ""}`;
const log = document.getElementById("log");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const meta = document.getElementById("meta");
const modePill = document.getElementById("mode-pill");
const picker = document.getElementById("picker");
const modal = document.getElementById("modal");
const modalBody = document.getElementById("modal-body");
const modalTitle = document.getElementById("modal-title");

/* ---------- Slash command picker ---------- */
const SLASH_COMMANDS = [
  { cmd: "/help",     args: "",                                           desc: "Show available commands" },
  { cmd: "/mode",     args: "chat|research|analyze|edit|full",            desc: "Change task mode (tool scope)" },
  { cmd: "/provider", args: "openai|ollama|claude-cli|claude-cli-raw",    desc: "Switch LLM provider (auto-picks default model)" },
  { cmd: "/model",    args: "<model-name>",                               desc: "Change model for current provider" },
  { cmd: "/context",  args: "auto|eos|generic",                           desc: "Switch context (EmptyOS vs generic coding)" },
  { cmd: "/vault",    args: "<path>|off",                                 desc: "Tell agent where to find your notes/schedule/projects" },
  { cmd: "/reset",    args: "",                                           desc: "Clear conversation (keep config)" },
  { cmd: "/yolo",     args: "on|off",                                     desc: "Toggle skip approval prompts for destructive tools" },
  { cmd: "/status",   args: "",                                           desc: "Show current provider/model/mode/context" },
];
const ARG_CHOICES = {
  "/mode":     ["chat", "research", "analyze", "edit", "full"],
  "/provider": ["openai", "ollama", "claude-cli", "claude-cli-raw"],
  "/context":  ["auto", "eos", "generic"],
  "/yolo":     ["on", "off"],
};
let pickerActive = 0;
let pickerItems = [];
let currentProvider = "openai";            // updated from hello/state
let modelCache = {};                        // { provider: [models...] }
let modelFetchPending = {};                 // { provider: Promise }

async function fetchModels(provider) {
  if (modelCache[provider]) return modelCache[provider];
  if (modelFetchPending[provider]) return modelFetchPending[provider];
  modelFetchPending[provider] = (async () => {
    try {
      const tokenQS = token ? `&t=${encodeURIComponent(token)}` : "";
      const r = await fetch(`/api/models?provider=${encodeURIComponent(provider)}${tokenQS}`);
      const data = await r.json();
      modelCache[provider] = data.models || [];
      return modelCache[provider];
    } catch (e) {
      return [];
    } finally {
      delete modelFetchPending[provider];
    }
  })();
  return modelFetchPending[provider];
}

function _showPicker(items, loadingHint) {
  pickerItems = items;
  if (items.length === 0 && !loadingHint) {
    picker.classList.remove("open");
    return;
  }
  pickerActive = Math.max(0, Math.min(pickerActive, items.length - 1));
  const rows = items.map((it, i) =>
    `<div class="pick-item ${i === pickerActive ? "active" : ""}" data-i="${i}">
      <span class="pick-cmd">${it.label}</span>
      <span class="pick-args">${it.suffix || ""}</span>
      <span class="pick-desc">${it.desc || ""}</span>
    </div>`
  ).join("");
  const hint = loadingHint
    ? `<div class="pick-hint">${loadingHint}</div>`
    : `<div class="pick-hint">Tab/Enter = complete · ↑↓ = navigate · Esc = cancel</div>`;
  picker.innerHTML = rows + hint;
  picker.classList.add("open");
}

async function renderPicker() {
  const text = input.value;
  if (!text.startsWith("/")) {
    picker.classList.remove("open");
    pickerItems = [];
    return;
  }
  const parts = text.split(/\s+/);
  const cmdPart = parts[0];
  const hasSpace = text.includes(" ");

  if (!hasSpace) {
    // Level 1: command completion
    const items = SLASH_COMMANDS
      .filter(c => c.cmd.startsWith(cmdPart) || c.cmd.toLowerCase().includes(cmdPart.slice(1).toLowerCase()))
      .map(c => ({ label: c.cmd, suffix: c.args ? ` <${c.args}>` : "", desc: c.desc, complete: c.cmd + (c.args ? " " : "") }));
    _showPicker(items);
    return;
  }

  // Level 2: argument suggestions
  const typed = parts.slice(1).join(" ").toLowerCase();

  // Special case: /model → fetch from API based on current provider
  if (cmdPart === "/model") {
    // Show a "loading" hint immediately
    const cached = modelCache[currentProvider];
    if (!cached) {
      _showPicker([], `Fetching models for ${currentProvider}…`);
    }
    const models = await fetchModels(currentProvider);
    // Re-read input in case user typed more while fetching
    const nowText = input.value;
    if (!nowText.startsWith("/model ")) return;   // user changed command
    const nowTyped = nowText.slice("/model ".length).toLowerCase();
    const items = models
      .filter(m => m.toLowerCase().includes(nowTyped))
      .map(m => ({
        label: m || "<default>",
        suffix: "",
        desc: `/model ${m}` + (m === "" ? "  (let claude pick)" : ""),
        complete: `/model ${m}`,
      }));
    _showPicker(items, `${models.length} model(s) for ${currentProvider}`);
    return;
  }

  // Generic static ARG_CHOICES
  const choices = ARG_CHOICES[cmdPart];
  if (!choices) {
    picker.classList.remove("open");
    return;
  }
  const items = choices
    .filter(v => v.toLowerCase().startsWith(typed))
    .map(v => ({ label: v, suffix: "", desc: `${cmdPart} ${v}`, complete: `${cmdPart} ${v}` }));
  _showPicker(items);
}

function applyPick(index) {
  if (!pickerItems[index]) return;
  input.value = pickerItems[index].complete;
  picker.classList.remove("open");
  input.focus();
  // Trigger a new render (may show arg suggestions next)
  requestAnimationFrame(renderPicker);
}

picker.addEventListener("click", (e) => {
  const item = e.target.closest(".pick-item");
  if (!item) return;
  applyPick(parseInt(item.dataset.i, 10));
});

const MODE_HINTS = {
  chat:     "no tools · reasoning only",
  research: "read-only · grep & list",
  analyze:  "read + 1 output file",
  edit:     "full write & edit",
  full:     "all tools incl. bash",
};
function setMode(mode) {
  const m = mode || "full";
  modePill.className = `mode-pill mode-${m}`;
  modePill.innerHTML = `${m.toUpperCase()} <span class="mode-hint">${MODE_HINTS[m] || ""}</span>`;
  document.body.dataset.mode = m;
}
function setMeta(text) {
  meta.textContent = text;
}

let ws = null;
let pendingApprovalId = null;
let currentAgentMsg = null;

function addMsg(cls, text) {
  const d = document.createElement("div");
  d.className = `msg ${cls}`;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}
function mdRender(text) {
  // minimal markdown: code blocks, inline code, bold, italic, headers, links, bullets
  // Protect code blocks first so their content isn't touched by other regexes
  const codeBlocks = [];
  let html = text.replace(/```(\w*)\n([\s\S]*?)```/g,
    (_, lang, code) => {
      const safe = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      codeBlocks.push(`<pre><code>${safe}</code></pre>`);
      return `\x00CODEBLOCK${codeBlocks.length - 1}\x00`;
    });

  // Protect inline code similarly
  const inlineCodes = [];
  html = html.replace(/`([^`\n]+)`/g, (_, code) => {
    const safe = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    inlineCodes.push(`<code>${safe}</code>`);
    return `\x00INLINE${inlineCodes.length - 1}\x00`;
  });

  // Escape the rest
  html = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Headers (must run before bold since #'s don't conflict)
  html = html.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.*)$/gm, "<h1>$1</h1>");

  // Links: [text](url) — rendered as clickable anchor
  html = html.replace(
    /\[([^\]]+)\]\(([^)\s]+)\)/g,
    (_, label, url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`
  );

  // Bare URLs auto-link (after [text](url) so we don't double-wrap)
  html = html.replace(
    /(^|\s)(https?:\/\/[^\s<]+)/g,
    (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );

  // Bold / italic
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  // Bullet lists — wrap consecutive "- " or "* " lines in <ul>
  html = html.replace(
    /(^|\n)((?:[ \t]*[-*] [^\n]+\n?)+)/g,
    (_, pre, block) => {
      const items = block.trim().split(/\n/).map(l =>
        `<li>${l.replace(/^[ \t]*[-*] /, "")}</li>`
      ).join("");
      return `${pre}<ul>${items}</ul>`;
    }
  );

  // Line breaks (single newlines → <br>, but not inside <ul>/<pre>)
  html = html.replace(/\n(?!<\/?(ul|li|h\d|pre))/g, "<br>");

  // Restore code placeholders
  html = html.replace(/\x00CODEBLOCK(\d+)\x00/g, (_, i) => codeBlocks[+i]);
  html = html.replace(/\x00INLINE(\d+)\x00/g, (_, i) => inlineCodes[+i]);

  return html;
}
function connect() {
  setMeta("connecting...");
  ws = new WebSocket(wsUrl);
  ws.onopen = () => setMeta("connected");
  ws.onclose = () => { setMeta("disconnected — retrying..."); setTimeout(connect, 2000); };
  ws.onerror = (e) => setMeta("error");
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "hello") {
      setMode(m.mode);
      currentProvider = m.provider;
      setMeta(`${m.provider}/${m.model || "default"} · ${m.context}${m.vault ? ` · vault:✓` : ""}`);
      addMsg("tool", `session: ${m.session_name}`);
      addMsg("tool", `sandbox: ${m.sandbox}`);
      if (m.vault) addMsg("tool", `vault: ${m.vault}  (agent knows to search here)`);
      if (m.provider === "claude-cli") {
        addMsg("warn", "note: claude-cli runs its own tools — approval gate disabled");
      }
    } else if (m.type === "state") {
      setMode(m.mode);
      currentProvider = m.provider;
      setMeta(`${m.provider}/${m.model || "default"} · ${m.context}${m.vault ? ` · vault:✓` : ""}`);
    } else if (m.type === "tool_call") {
      addMsg("tool", `-> ${m.name}(${m.args_summary})`);
    } else if (m.type === "agent_text") {
      if (!currentAgentMsg) {
        currentAgentMsg = document.createElement("div");
        currentAgentMsg.className = "msg agent";
        log.appendChild(currentAgentMsg);
      }
      currentAgentMsg.innerHTML = mdRender(m.text);
      log.scrollTop = log.scrollHeight;
    } else if (m.type === "done") {
      currentAgentMsg = null;
      sendBtn.disabled = false;
      input.focus();
    } else if (m.type === "error") {
      addMsg("err", "error: " + m.message);
      sendBtn.disabled = false;
    } else if (m.type === "system") {
      addMsg("ok", m.message);
    } else if (m.type === "approval_request") {
      pendingApprovalId = m.id;
      modalTitle.textContent = "Tool request: " + m.tool_name;
      let html = `<pre>${JSON.stringify(m.args, null, 2)}</pre>`;
      if (m.warning) html = `<div class="msg warn">${m.warning}</div>` + html;
      modalBody.innerHTML = html;
      modal.classList.add("open");
    }
  };
}

function send() {
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== 1) return;
  addMsg("user", text);
  ws.send(JSON.stringify({type: "user_message", text}));
  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  // Picker keyboard nav (only when open)
  if (picker.classList.contains("open")) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      pickerActive = Math.min(pickerItems.length - 1, pickerActive + 1);
      renderPicker();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      pickerActive = Math.max(0, pickerActive - 1);
      renderPicker();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      applyPick(pickerActive);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      picker.classList.remove("open");
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // Enter with picker open: complete instead of send (unless only 1 exact match)
      e.preventDefault();
      applyPick(pickerActive);
      return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(200, input.scrollHeight) + "px";
  pickerActive = 0;
  renderPicker();
});
input.addEventListener("blur", () => {
  // Close picker on blur, but delay so clicks register first
  setTimeout(() => picker.classList.remove("open"), 150);
});
input.addEventListener("focus", () => {
  if (input.value.startsWith("/")) renderPicker();
});
modal.querySelectorAll("button[data-d]").forEach(b => {
  b.addEventListener("click", () => {
    if (!pendingApprovalId || !ws) return;
    ws.send(JSON.stringify({
      type: "approval", id: pendingApprovalId, decision: b.dataset.d,
    }));
    pendingApprovalId = null;
    modal.classList.remove("open");
  });
});
connect();
</script>
</body></html>"""


def _build_server_session(cwd: Path, args) -> "Session":
    """Build a Session for the web server (fresh per connection, or resume)."""
    # Mirrors the new-session branch of repl_main but pulls defaults from args
    context = detect_context(cwd, args.context if args.context != "auto" else None)

    if args.sandbox:
        sandbox = Path(args.sandbox).resolve()
    elif context == "eos" and cwd.resolve() == REPO_ROOT.resolve():
        sandbox = REPO_ROOT / "_sandbox" / f"serve-{_cwd_hash(cwd)}"
    else:
        sandbox = cwd
    sandbox.mkdir(parents=True, exist_ok=True)

    provider = args.provider or ("openai" if os.environ.get("OPENAI_API_KEY") else "ollama")
    model = args.model or PROVIDER_DEFAULT_MODEL.get(provider, "")
    mode = args.mode or "full"

    session = Session.new(
        cwd=cwd,
        sandbox=sandbox,
        provider=provider,
        model=model,
        mode=mode,
        context=context,
        name=args.name,
        yolo=args.yolo,
        max_iterations=args.max_iterations,
    )
    session.rebuild_system_prompt()
    return session


class WebApprover:
    """Approval gate for web-mode turns. Reads live session flags on each call
    so /yolo toggled mid-run takes effect immediately.

    MVP policy: web mode can't block the sync tool-loop for user input. So we
    deny destructive ops when /yolo is off. Users toggle /yolo on to allow.
    Real bidirectional approval needs an asyncio.Queue bridge — see plan B5.
    """

    def __init__(self, session: "Session"):
        self.session = session

    def request(self, tool_name: str, args: dict) -> str:
        if tool_name not in _DESTRUCTIVE_TOOLS:
            return "allow"
        if self.session.yolo or tool_name in self.session.always_allow:
            return "allow"
        return "deny"


def serve_main(args) -> None:
    """Run the web CLI server: single HTML page + WebSocket endpoint."""
    try:
        import asyncio

        import uvicorn
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse
    except ImportError as e:
        print(f"ERROR: web CLI needs fastapi + uvicorn: {e}")
        print("Install: pip install fastapi uvicorn")
        sys.exit(1)

    token = args.token or os.environ.get("EOS_AGENT_TOKEN", "")
    app = FastAPI(title="eos-agent web")
    cwd = Path.cwd()

    def _auth_ok(req_token: str | None) -> bool:
        if not token:
            return True  # no token configured = open (localhost usage)
        return req_token == token

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse(WEB_CHAT_HTML)

    @app.get("/health")
    async def health():
        return {"ok": True, "version": 1}

    @app.get("/api/models")
    async def api_models(provider: str = "openai"):
        """List suggested/available models for a provider.

        Ollama: live fetch from /api/tags (user's installed models).
        OpenAI/Claude: static curated list (full OpenAI model list is 100+ and mostly noise).
        """
        if provider == "ollama":
            try:

                def _fetch_tags():
                    req = urllib.request.Request("http://localhost:11434/api/tags")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return json.loads(resp.read().decode("utf-8"))

                data = await asyncio.to_thread(_fetch_tags)
                names = [m["name"] for m in data.get("models", [])]
                # put qwen/llama variants first, embeddings last
                names.sort(
                    key=lambda n: (
                        "embed" in n.lower() or "bge" in n.lower() or "nomic" in n.lower(),
                        not n.startswith(("qwen", "llama", "deepseek", "glm", "gemma")),
                        n,
                    )
                )
                return {"provider": "ollama", "models": names}
            except Exception as e:
                return {"provider": "ollama", "models": [], "error": str(e)}
        elif provider == "openai":
            return {
                "provider": "openai",
                "models": [
                    "gpt-5.4",
                    "gpt-5.4-mini",
                    "gpt-5.4-nano",
                    "gpt-5.4-pro",
                    "gpt-5",
                    "gpt-5-mini",
                    "gpt-5-nano",
                    "gpt-5-pro",
                    "gpt-5-codex",
                    "gpt-5.3-codex",
                    "gpt-4o",
                    "gpt-4o-mini",
                    "gpt-4.1",
                    "gpt-4.1-mini",
                    "o3",
                    "o4-mini",
                ],
            }
        elif provider in ("claude-cli", "claude-cli-raw"):
            return {
                "provider": provider,
                "models": [
                    "",  # let claude pick its default
                    "sonnet",
                    "opus",
                    "haiku",
                ],
            }
        return {"provider": provider, "models": []}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        qs_token = ws.query_params.get("t")
        if not _auth_ok(qs_token):
            await ws.send_json({"type": "error", "message": "auth failed"})
            await ws.close(code=4401)
            return

        # Fresh session per connection (or resume)
        if args.resume:
            try:
                session = Session.load(args.resume)
            except Exception as e:
                await ws.send_json({"type": "error", "message": f"resume failed: {e}"})
                await ws.close()
                return
        else:
            session = _build_server_session(cwd, args)

        async def send(msg: dict):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

        await send(
            {
                "type": "hello",
                "session_name": session.name,
                "provider": session.provider,
                "model": session.model or "",
                "mode": session.mode,
                "context": session.context,
                "sandbox": session.sandbox,
                "vault": session.vault or "",
            }
        )

        # Main recv loop
        try:
            while True:
                data = await ws.receive_json()
                mtype = data.get("type")
                if mtype == "user_message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    # Slash command?
                    if text.startswith("/"):
                        await _web_handle_slash(session, text, send)
                        await send({"type": "done"})
                        continue
                    # Normal turn
                    await _web_run_turn(session, text, ws, send)
                    await send({"type": "done"})
                elif mtype == "approval":
                    # Not used in MVP (sync approver can't block on async) — informational
                    pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            await send({"type": "error", "message": f"server error: {e}"})
        finally:
            try:
                session.save()
            except Exception:
                pass

    # Banner
    addr_disp = "0.0.0.0" if args.bind == "0.0.0.0" else args.bind
    print("eos-agent serve")
    print(f"  cwd:      {cwd}")
    print(f"  context:  {detect_context(cwd, args.context if args.context != 'auto' else None)}")
    print(f"  provider: {args.provider or 'auto'}")
    print(f"  model:    {args.model or 'default'}")
    print(f"  mode:     {args.mode or 'full'}")
    print(f"  auth:     {'token required' if token else 'OPEN (no token)'}")
    print(f"  url:      http://{addr_disp}:{args.port}{f'?t={token}' if token else ''}")
    if args.bind == "0.0.0.0":
        print("  phone:    add Tailscale (tailscale.com) on both devices, then access")
        print(
            f"            http://<this-machine-tailscale-name>:{args.port}{f'?t={token}' if token else ''}"
        )
    print()

    uvicorn.run(app, host=args.bind, port=args.port, log_level="info")


async def _web_run_turn(session, user_text: str, ws, send) -> None:
    """Execute one user turn over the web socket. No streaming yet — runs sync _run_turn in executor."""
    import asyncio

    # Append user message
    session.append_message({"role": "user", "content": user_text})
    tools_subset = filtered_tools(session.mode)
    sandbox = Path(session.sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    global _SESSION_TODO
    _SESSION_TODO = list(session.todos)

    max_loop = 1 if session.mode == "chat" or not tools_subset else session.max_iterations
    iteration = 0
    final_text = ""
    approver = WebApprover(session)

    loop = asyncio.get_running_loop()
    while iteration < max_loop:
        iteration += 1
        # Run _run_turn in a thread since it's sync + does subprocess/urllib.
        # Use functools.partial to bind loop variables explicitly — avoids the
        # late-binding closure trap (B023) and is awaited inline, so capture
        # would be safe in practice anyway.
        try:
            turn = await loop.run_in_executor(
                None,
                functools.partial(
                    _run_turn,
                    session.provider,
                    session.model,
                    session.messages,
                    tools_subset,
                    sandbox,
                    approver=approver,
                    trace=None,
                    iteration=iteration,
                    mode=session.mode,
                ),
            )
        except Exception as e:
            await send({"type": "error", "message": str(e)})
            break

        # Emit tool call traces
        for tc in turn["tool_calls"]:
            args_summary = _fmt_tool_args(tc["args"])
            await send(
                {
                    "type": "tool_call",
                    "name": tc["name"],
                    "args_summary": args_summary[:200],
                }
            )

        if turn["error"]:
            await send({"type": "error", "message": turn["error"]})
            break
        if turn["done"]:
            final_text = turn["content"]
            break

    # Sync todos
    session.set("todos", list(_SESSION_TODO))
    # Send final text
    if final_text:
        await send({"type": "agent_text", "text": final_text})
    try:
        session.save()
    except Exception:
        pass


async def _web_handle_slash(session, line: str, send) -> None:
    """Handle slash commands over the web socket. Minimal subset."""
    parts = line[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    msg = ""

    if cmd in ("help", "h"):
        commands = [
            "/help — this help",
            "/mode <chat|research|analyze|edit|full>",
            "/provider <openai|ollama|claude-cli|claude-cli-raw>  (auto-picks default model)",
            "/model <name>",
            "/context <auto|eos|generic>",
            "/vault <path|off>  — where agent looks for notes/schedule/projects",
            "/reset — clear conversation",
            "/yolo <on|off> — toggle destructive-op approval gate",
            "/status — current state",
        ]
        msg = "Commands:\n" + "\n".join(commands)
    elif cmd == "mode":
        new_mode = args.strip()
        if new_mode in MODE_TOOLS:
            session.set("mode", new_mode)
            session.rebuild_system_prompt()
            msg = f"mode -> {new_mode}"
        else:
            msg = f"unknown mode: {new_mode}. choices: {', '.join(MODE_TOOLS)}"
    elif cmd == "provider":
        new_p = args.strip()
        if new_p in ("openai", "ollama", "claude-cli", "claude-cli-raw"):
            session.set("provider", new_p)
            default_model = PROVIDER_DEFAULT_MODEL.get(new_p, "")
            session.set("model", default_model)
            msg = f"provider -> {new_p} (model -> {default_model or '<claude default>'})"
            if new_p == "claude-cli":
                msg += " — note: claude-cli runs its own tools, approval gate disabled"
        else:
            msg = f"unknown provider: {new_p}"
    elif cmd == "model":
        new_model = args.strip()
        if not new_model:
            msg = f"current model: {session.model or '<default>'}"
        else:
            session.set("model", new_model)
            msg = f"model -> {new_model}"
    elif cmd == "context":
        new_c = args.strip() or "auto"
        if new_c == "auto":
            resolved = detect_context(Path(session.cwd), None)
        elif new_c in ("eos", "generic"):
            resolved = new_c
        else:
            msg = "choices: auto, eos, generic"
            await send({"type": "system", "message": msg})
            return
        session.set("context", resolved)
        session.rebuild_system_prompt()
        msg = f"context -> {resolved}"
    elif cmd == "vault":
        arg = args.strip()
        if not arg:
            msg = f"current vault: {session.vault or '(not set)'}"
        elif arg.lower() == "off":
            session.set("vault", "")
            session.rebuild_system_prompt()
            msg = "vault -> (cleared)"
        else:
            p = Path(arg).resolve()
            if not p.exists():
                msg = f"path does not exist: {p}"
            else:
                session.set("vault", str(p))
                session.rebuild_system_prompt()
                msg = f"vault -> {p}"
    elif cmd == "reset":
        session.clear_messages(keep_system=True)
        msg = "conversation cleared"
    elif cmd == "yolo":
        arg = args.strip().lower()
        if arg == "on":
            session.set("yolo", True)
        elif arg == "off":
            session.set("yolo", False)
        else:
            session.set("yolo", not session.yolo)
        msg = f"yolo: {'ON' if session.yolo else 'off'}"
    elif cmd == "status":
        msg = (
            f"provider={session.provider} model={session.model or '<default>'} "
            f"mode={session.mode} context={session.context} "
            f"yolo={'ON' if session.yolo else 'off'} sandbox={session.sandbox}"
        )
    else:
        msg = f"unknown command: /{cmd}. type /help"

    await send({"type": "system", "message": msg})
    # Push state update so header reflects changes
    await send(
        {
            "type": "state",
            "provider": session.provider,
            "model": session.model or "",
            "mode": session.mode,
            "context": session.context,
            "vault": session.vault or "",
        }
    )


def main():
    ap = argparse.ArgumentParser(description="eos-agent — Claude-Code-equivalent shell for EmptyOS")
    sub = ap.add_subparsers(dest="cmd", required=True)

    eval_p = sub.add_parser("eval", help="Run the conversation-mode test suite")
    eval_p.add_argument(
        "--provider", default="openai", choices=["openai", "ollama", "claude-cli", "claude-cli-raw"]
    )
    eval_p.add_argument("--model", default=None)
    eval_p.add_argument("--test", help="Filter to one test by id prefix")

    exec_p = sub.add_parser("exec", help="One-shot: execute a prompt")
    exec_p.add_argument("prompt", help="Task for the agent")
    exec_p.add_argument(
        "--provider", default="openai", choices=["openai", "ollama", "claude-cli", "claude-cli-raw"]
    )
    exec_p.add_argument("--model", default=None)
    exec_p.add_argument("--sandbox", help="Sandbox directory (default: temp dir)")
    exec_p.add_argument(
        "--mode",
        default="full",
        choices=["chat", "research", "analyze", "edit", "full"],
        help="Tool scope: chat (no tools), research (read-only), analyze (+write), edit (full), full (default)",
    )

    repl_p = sub.add_parser("repl", help="Interactive shell (like claude code)")
    repl_p.add_argument(
        "--provider", default=None, choices=["openai", "ollama", "claude-cli", "claude-cli-raw"]
    )
    repl_p.add_argument("--model", default=None)
    repl_p.add_argument(
        "--mode",
        default=None,
        choices=["chat", "research", "analyze", "edit", "full"],
        help="Tool scope (default: full, or loaded from resumed session)",
    )
    repl_p.add_argument("--sandbox", default=None, help="Sandbox directory (default: cwd or auto)")
    repl_p.add_argument("--context", default="auto", choices=["auto", "eos", "generic"])
    repl_p.add_argument("--resume", default=None, help="Resume saved session by name")
    repl_p.add_argument("--name", default=None, help="Session name (default: auto)")
    repl_p.add_argument(
        "--yolo", action="store_true", help="Skip approval prompts for destructive tools"
    )
    repl_p.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Max tool-loop iterations per turn (default 20)",
    )

    serve_p = sub.add_parser("serve", help="Web CLI: run a FastAPI server with chat UI")
    serve_p.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; use 0.0.0.0 for remote access via Tailscale/LAN)",
    )
    serve_p.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    serve_p.add_argument(
        "--token",
        default=None,
        help="Shared secret token for auth. Also reads EOS_AGENT_TOKEN env var.",
    )
    serve_p.add_argument(
        "--provider", default=None, choices=["openai", "ollama", "claude-cli", "claude-cli-raw"]
    )
    serve_p.add_argument("--model", default=None)
    serve_p.add_argument(
        "--mode", default="full", choices=["chat", "research", "analyze", "edit", "full"]
    )
    serve_p.add_argument("--sandbox", default=None)
    serve_p.add_argument("--context", default="auto", choices=["auto", "eos", "generic"])
    serve_p.add_argument(
        "--resume", default=None, help="Resume saved session by name on every connection"
    )
    serve_p.add_argument("--name", default=None)
    serve_p.add_argument("--yolo", action="store_true")
    serve_p.add_argument("--max-iterations", type=int, default=20)

    args = ap.parse_args()

    if args.cmd == "eval":
        model = args.model or PROVIDER_DEFAULT_MODEL.get(args.provider, "")
        run_eval(args.provider, model, args.test)
    elif args.cmd == "exec":
        model = args.model or PROVIDER_DEFAULT_MODEL.get(args.provider, "")
        run_exec(args.provider, model, args.prompt, args.sandbox, args.mode)
    elif args.cmd == "repl":
        repl_main(args)
    elif args.cmd == "serve":
        serve_main(args)


if __name__ == "__main__":
    main()
