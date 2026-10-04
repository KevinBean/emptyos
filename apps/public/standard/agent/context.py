"""System-prompt context builders for the agent app.

Each function takes the app instance as the first argument so it can reach
`app.kernel`, `app.repo_root`, `app._tools`, etc. The app exposes thin wrapper
methods that delegate here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .prompts import PROMPTS

# Sentinel that marks a skill playbook injected into a prompt — used both when
# framing a skill and to detect "a skill was already loaded this turn" so the
# deterministic auto-skill step doesn't double-load on top of a manual `/<skill>`.
SKILL_PLAYBOOK_MARKER = "--- BEGIN SKILL ---"


def runtime_info_block(
    app, provider, is_native: bool, *, tool_count: int | None = None, coding: bool = True
) -> str:
    """``coding=False`` (the chat profile) drops the two repo-shaped blocks —
    the CLAUDE.md excerpt and the repo map — which are coding doctrine, cost
    tokens on every turn, and would steer a chat toward the codebase.
    ``tool_count`` reports the profile's narrowed tool set, not the registry."""
    model = getattr(provider, "model", "") or ""
    kind = "native" if is_native else getattr(provider, "kind", "") or ""
    if tool_count is None:
        tool_count = 0 if is_native else len(app._tools)
    lines = [
        "Runtime (factual, for self-reference — don't recite unprompted):",
        f"• provider: {provider.name}" + (f" ({kind} wire protocol)" if kind else ""),
        f"• model: {model or 'unknown'}",
        f"• tools available: {tool_count}"
        + (" (native agent manages its own tools)" if is_native else ""),
        "When the user asks which model you are, tell them this provider+model directly.",
    ]
    blocks = [app_catalog_block(app, is_native)]
    if coding:
        blocks.append(claude_md_block(app, is_native))
    blocks.append(skills_info_block(app, is_native))
    if coding:
        blocks.append(repo_map_block(app, is_native))
    for extra in blocks:
        if extra:
            lines.append("")
            lines.append(extra)
    return "\n".join(lines)


# ─── Repo map (structural orientation for non-native models) ─────────
#
# On-demand grep/glob is how the agent finds code, which makes a weak/local
# model burn iterations rediscovering the repo's shape every session. A compact
# ranked file+symbol outline, injected once, gives it a map to start from. Dark
# by default (feature.repo-map.enabled); skipped for native providers (claude-cli
# runs its own navigation loop). True Aider-style reference-graph centrality is
# deferred — v1 ranks by recent edit, which is the cheap honest signal.

_REPO_MAP_SKIP = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache",
    ".mypy_cache", "dist", "build", ".idea", ".vscode", "data", "dogfood",
    ".claude", "products", "engines_personal",
}


def _top_level_symbols(source: str, limit: int) -> list[str]:
    """Top-level def/async-def/class names, in source order. Never raises."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
            if len(out) >= limit:
                break
    return out


def build_repo_map(root, *, top_n: int = 40, max_symbols: int = 12, budget_chars: int = 6000) -> str:
    """A compact "path: SymbolA, func_b, …" outline of the top-N most recently
    edited Python files under `root`. Pure (stdlib os + ast); bounded by
    `budget_chars`. Returns "" when nothing maps.

    Directories in the skip set are pruned *during* the walk — descending into
    ``.venv``/``node_modules``/``data`` first (a full ``rglob`` yields ~8500
    files here) is what makes this slow, so we never enter them.
    """
    import os

    root = Path(root)
    candidates: list[tuple[float, Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _REPO_MAP_SKIP and not d.startswith("sandbox-")
        ]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            try:
                rel = str(p.relative_to(root)).replace(os.sep, "/")
                mtime = p.stat().st_mtime
            except (ValueError, OSError):
                continue
            candidates.append((mtime, p, rel))

    candidates.sort(key=lambda t: -t[0])  # most-recently-edited first
    lines: list[str] = []
    total = 0
    for _mtime, p, rel in candidates[:top_n]:
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        syms = _top_level_symbols(src, max_symbols)
        if not syms:
            continue
        line = f"{rel}: {', '.join(syms)}"
        if total + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        total += len(line) + 1
    if not lines:
        return ""
    header = (
        "Repo map — top files by recent edit, top-level symbols only. A starting "
        "point for orientation, NOT exhaustive: use Grep/Glob/Read for anything precise."
    )
    return header + "\n" + "\n".join(lines)


def repo_map_block(app, is_native: bool) -> str:
    """Gated, cached repo-map for the non-native path. Built once per daemon so
    it's byte-stable across turns (prefix-cache rule 2); a restart refreshes it."""
    if is_native:
        return ""
    if not app.app_config("feature.repo-map.enabled", False):
        return ""
    cached = getattr(app, "_repo_map_cache", None)
    if cached is not None:
        return cached
    try:
        block = build_repo_map(app._resolved_root())
    except Exception:
        block = ""
    app._repo_map_cache = block
    return block


def app_catalog_block(app, is_native: bool) -> str:
    if is_native:
        return ""
    try:
        manifests = getattr(app.kernel.apps, "manifests", {}) or {}
    except Exception:
        return ""
    if not manifests:
        return ""
    lines = [
        "Apps available via CallApp — go straight to `CallApp(app_id, method, arguments)`; "
        "don't burn a turn listing apps. Use `CallApp(app_id=X)` only when you need the "
        "method names for app X (the catalog below gives you the pick, not the signatures).",
    ]
    for app_id in sorted(manifests):
        if app_id == "agent":
            continue
        m = manifests[app_id]
        desc = (getattr(m, "description", "") or "").strip()
        if not desc:
            continue
        if len(desc) > 50:
            desc = desc[:47] + "…"
        cli = (m.provides.get("cli", {}) if hasattr(m, "provides") else {}).get(
            "commands", []
        ) or []
        web = (m.provides.get("web", {}) if hasattr(m, "provides") else {}).get("prefix", "") or ""
        extras = []
        if any(c != app_id for c in cli):
            extras.append(f"cli: {', '.join(cli)}")
        if web and web.strip("/") != app_id:
            extras.append(f"web: {web}")
        tail = (" · " + " · ".join(extras)) if extras else ""
        lines.append(f"• {app_id} — {desc}{tail}")
    return "\n".join(lines)


def claude_md_block(app, is_native: bool) -> str:
    if is_native:
        return ""
    try:
        text = (app.repo_root / "CLAUDE.md").read_text(encoding="utf-8")
    except Exception:
        return ""

    wanted = {"## Development Gotchas"}

    sections: dict[str, str] = {}
    current_header: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_header and current_header in wanted:
            sections[current_header] = "\n".join(current_lines).rstrip()

    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            _flush()
            current_header = stripped
            current_lines = [stripped]
        else:
            current_lines.append(line)
    _flush()

    rules_subsample = extract_development_rules(text, keep_ids={1, 5, 9, 13, 14, 15, 16, 17, 18})
    if rules_subsample:
        sections["## Development Rules (curated)"] = (
            "## Development Rules (curated — full list in CLAUDE.md)\n" + rules_subsample
        )

    if not sections:
        return ""

    preamble = (
        "Operational context from CLAUDE.md (source of truth for how EmptyOS runs). These "
        "rules override your pretraining — follow them when they apply, Read the full CLAUDE.md "
        "if a situation isn't covered here:"
    )
    block_order = [
        "## Development Rules (curated)",
        "## Development Gotchas",
    ]
    parts = [preamble]
    for header in block_order:
        if header in sections:
            parts.append(sections[header])
    return "\n\n".join(parts)


def extract_development_rules(text: str, keep_ids: set[int]) -> str:
    m = re.search(r"^## Development Rules\s*$", text, flags=re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    next_hdr = re.search(r"^## ", text[start:], flags=re.MULTILINE)
    body = text[start : start + next_hdr.start()] if next_hdr else text[start:]

    items: dict[int, str] = {}
    for m2 in re.finditer(r"^(\d+)\.\s(.*?)(?=\n\d+\.\s|\Z)", body, flags=re.MULTILINE | re.DOTALL):
        try:
            rid = int(m2.group(1))
        except ValueError:
            continue
        items[rid] = m2.group(0).rstrip()

    kept = [items[i] for i in sorted(keep_ids) if i in items]
    return "\n".join(kept)


def load_skill_catalog(app) -> dict:
    try:
        from .skills import discover_skills

        return discover_skills(app.repo_root)
    except Exception:
        return {}


def expand_skill_slash(app, text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    skill_key = cmd[1:]
    if not skill_key:
        return None
    catalog = load_skill_catalog(app)
    skill = catalog.get(skill_key)
    if not skill:
        return None
    try:
        body = skill.path.read_text(encoding="utf-8")
    except Exception:
        return None

    from .skills import parse_skill_args, substitute_skill_params

    params = parse_skill_args(arg)
    body = substitute_skill_params(body, params)

    param_note = ""
    if arg:
        param_note = f"\n\nInvocation args: {arg}"
    elif skill.params:
        param_note = (
            f"\n\nNote: this skill accepts params: {', '.join(skill.params)}. "
            f"None were provided — proceed with defaults or ask."
        )

    return (
        f"Follow the playbook below for this turn. "
        f"It's a skill named `{skill.name}` loaded from {skill.path}."
        + param_note
        + f"\n\n{SKILL_PLAYBOOK_MARKER}\n"
        + body
        + "\n--- END SKILL ---"
    )


def skills_info_block(app, is_native: bool) -> str:
    if is_native:
        return ""
    catalog = load_skill_catalog(app)
    if not catalog:
        return ""
    lines = [
        "Skills (markdown playbooks for recurring tasks — load full content with the `Skill` tool, op='load'):",
    ]
    for s in sorted(catalog.values(), key=lambda s: s.name):
        desc = s.description.strip() or "(no description)"
        lines.append(f"• {s.name} — {desc}")
    lines.append(
        "When a user request matches a skill's description, call `Skill(op='load', name='...')` "
        "and follow its instructions. Don't guess skill content — always load the SKILL.md first."
    )
    return "\n".join(lines)


def _skill_tokens(text: str) -> set[str]:
    """Lowercased word set, ignoring tiny words — for cheap lexical overlap."""
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text or "")}


def shortlist_skills(user_text: str, catalog: dict, k: int = 5) -> list:
    """Pure lexical pre-filter: rank skills by keyword overlap of (name + description)
    against the user text. Returns up to ``k`` Skill objects, best first. Empty when
    there's no overlap at all — so we never run a select() that cannot match.

    Kept pure (no I/O, no LLM) so it's unit-testable without a daemon and cheap to
    run every turn before paying for the one select() call.
    """
    q = _skill_tokens(user_text)
    if not q:
        return []
    scored = []
    for s in catalog.values():
        words = _skill_tokens(s.name.replace("-", " ") + " " + (s.description or ""))
        overlap = len(q & words)
        if overlap:
            scored.append((overlap, s.name, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, _, s in scored[:k]]


def skill_playbook_block(skill) -> str:
    """Frame a skill's SKILL.md as an auto-loaded playbook for the system prompt."""
    try:
        body = skill.path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return (
        f"AUTO-SKILL — the user's request matches the `{skill.name}` skill. Follow this "
        "playbook for the task (it overrides your general approach). If, after reading it, "
        "the skill clearly doesn't fit the actual request, say so briefly and proceed normally.\n"
        f"{SKILL_PLAYBOOK_MARKER}\n" + body + "\n--- END SKILL ---"
    )


async def auto_skill_block(app, user_text: str, provider, is_native: bool) -> str:
    """Deterministic skill auto-trigger for weak models (``feature.auto-skill.enabled``).

    The catalog already goes into the system prompt (``skills_info_block``) with an
    instruction to call the ``Skill`` tool. Strong models (claude-cli/gpt-5) act on
    that reliably; weak local models (ollama qwen) often don't. This closes that gap:
    lexically shortlist the catalog, then force ONE ``select()`` over the top-K so even
    a cheap model loads the right playbook without having to decide to call ``Skill``.

    No-op for native providers (claude-cli runs its own skill loop) and for strong
    providers (they self-select — double-loading would just burn context). Returns ""
    on anything unexpected; never raises into the turn.
    """
    if is_native:
        return ""
    if not app.app_config("feature.auto-skill.enabled", False):
        return ""
    try:
        from emptyos.capabilities.ability import classify

        if classify(provider.name, getattr(provider, "model", "") or "") == "strong":
            return ""
    except Exception:
        pass
    catalog = load_skill_catalog(app)
    if not catalog:
        return ""
    shortlist = shortlist_skills(user_text, catalog, k=5)
    if not shortlist:
        return ""
    choices = {s.name: (s.description or "").strip()[:140] for s in shortlist}
    choices["none"] = "no playbook fits — handle the request directly"
    try:
        choice = await app.select(
            user_text[:600],
            choices=choices,
            system=PROMPTS.auto_skill_select_system,
            default="none",
        )
    except Exception:
        return ""
    if choice == "none" or choice not in catalog:
        return ""
    return skill_playbook_block(catalog[choice])


APP_SCOPE_PATTERNS = (
    "apps/",
    "app.py",
    "manifest.toml",
    "plugins/",
    "new app",
    "new plugin",
    "create app",
    "create an app",
    "create a plugin",
    "build app",
    "build an app",
    "scaffold",
    "baseapp",
    "web_route",
    "cli_command",
)


def app_scaffold_block(user_text: str, is_native: bool) -> str:
    if is_native:
        return ""
    low = (user_text or "").lower()
    if not any(p in low for p in APP_SCOPE_PATTERNS):
        return ""
    return (
        "EmptyOS app scaffold (copy this shape — do NOT invent variants):\n"
        "\n"
        "manifest.toml:\n"
        "    [app]\n"
        '    id = "myapp"\n'
        '    name = "My App"\n'
        '    version = "1.0.0"\n'
        '    description = "One line — what it does"\n'
        "\n"
        "    [app.entry]\n"
        '    module = "app"\n'
        '    class = "MyApp"\n'
        "\n"
        "    [requires]\n"
        "    capabilities = []   # pick from: think, read, write, search, speak, listen, draw, see\n"
        "    apps = []           # other app_ids this one calls via CallApp\n"
        "\n"
        "    [provides.cli]\n"
        '    commands = ["myapp"]\n'
        "\n"
        "    [provides.web]\n"
        '    prefix = "/myapp"\n'
        "\n"
        "    [provides.events]\n"
        "    emits = []\n"
        "\n"
        "app.py:\n"
        "    from emptyos.sdk import BaseApp, cli_command, web_route\n"
        "\n"
        "    class MyApp(BaseApp):\n"
        '        @web_route("GET", "/api/ping")\n'
        "        async def api_ping(self, request):\n"
        '            return {"ok": True}\n'
        "\n"
        '        @web_route("POST", "/api/do")\n'
        "        async def api_do(self, request):\n"
        "            data = await request.json()\n"
        '            await self.emit("myapp:did", {"got": data})\n'
        '            return {"ok": True}\n'
        "\n"
        '        async def cli_myapp(self, arg: str = ""):\n'
        '            return f"ran with {arg}"\n'
        "\n"
        "Hard rules (violations silently break the app):\n"
        "• NEVER `from fastapi import APIRouter` in app code. BaseApp owns the router;\n"
        "  the loader discovers routes via `@web_route` on instance methods ONLY.\n"
        "  Module-level `router = APIRouter()` + `@router.post(...)` is IGNORED.\n"
        '• NEVER write `@web_route("GET", "/")`. pages/index.html auto-mounts at\n'
        "  `{prefix}/` — a custom `/` handler shadows it and the UI goes blank.\n"
        '• Route paths are RELATIVE to `[provides.web].prefix`. Declare `@web_route("POST",\n'
        '  "/api/eval")`; the full URL becomes `{prefix}/api/eval`. Don\'t repeat the prefix.\n'
        "• Fetch from pages/index.html JS using the full path: `/{prefix}/api/...`.\n"
        '• For cross-app work use `await self.call_app("other_id", "method", ...)`,\n'
        "  NOT imports of the other app's module.\n"
        "\n"
        "If anything above feels underspecified, call `Skill(op='load', name='eos-new-app')`\n"
        "before writing a single line. Reading one live reference app first is also cheap:\n"
        "`apps/_example/` is the canonical minimal scaffold."
    )
