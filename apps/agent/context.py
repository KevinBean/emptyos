"""System-prompt context builders for the agent app.

Each function takes the app instance as the first argument so it can reach
`app.kernel`, `app.repo_root`, `app._tools`, etc. The app exposes thin wrapper
methods that delegate here.
"""

from __future__ import annotations

import re


def runtime_info_block(app, provider, is_native: bool) -> str:
    model = getattr(provider, "model", "") or ""
    kind = "native" if is_native else getattr(provider, "kind", "") or ""
    tool_count = 0 if is_native else len(app._tools)
    lines = [
        "Runtime (factual, for self-reference — don't recite unprompted):",
        f"• provider: {provider.name}" + (f" ({kind} wire protocol)" if kind else ""),
        f"• model: {model or 'unknown'}",
        f"• tools available: {tool_count}"
        + (" (native agent manages its own tools)" if is_native else ""),
        "When the user asks which model you are, tell them this provider+model directly.",
    ]
    for extra in (
        app_catalog_block(app, is_native),
        claude_md_block(app, is_native),
        skills_info_block(app, is_native),
    ):
        if extra:
            lines.append("")
            lines.append(extra)
    return "\n".join(lines)


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
        from apps.agent.skills import discover_skills

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

    from apps.agent.skills import parse_skill_args, substitute_skill_params

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
        + "\n\n--- BEGIN SKILL ---\n"
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
