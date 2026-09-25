"""Session profiles — what KIND of conversation an agent session is.

An agent session used to mean one thing: a coding companion over the EmptyOS
repo, with the coding system prompt, every tool (Bash, Write, Edit, Python…),
an orient pass that classifies the request against CLAUDE.md, and the app
scaffold block. The chat home (/portal/, chat-first) needs the same engine —
streaming, tool cards, consent, persistence — for ordinary conversation, where
all four of those are wrong: a "what's on my week?" should not be classified as
a build/debug task, should not be offered `Bash`, and should not have a coding
persona.

A profile is that difference, as data. Pure module: no ``self``, no kernel, no
relative imports, so it loads and tests in isolation (tests/test_unit_agent_profiles.py).
The legacy value ``""`` (every session created before profiles existed, and
every session /agent/ creates) means ``coding`` — unchanged behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

MCP_PREFIX = "mcp__"


@dataclass(frozen=True)
class Profile:
    name: str
    #: Tool names offered to the model, or ``None`` for the whole registry.
    tools: frozenset[str] | None
    #: The pre-turn orient/classify pass (coding-rule lookup). Costs a model call.
    orient: bool
    #: The EmptyOS app-scaffold hint block.
    scaffold: bool
    #: The dark-flagged skill auto-trigger. Skills here are mostly dev playbooks.
    auto_skill: bool
    #: The dark-flagged episodic recall block. For tool-capable providers it is
    #: prepended to the user's message, which is then persisted — so a chat's
    #: stored history would show text the user never typed.
    episodic: bool
    #: Key into the agent's PROMPTS registry for the default system prompt;
    #: ``""`` means the engine's coding default.
    system_prompt_key: str
    #: The /plan read-only banner (it names coding tools: Write, Edit, Bash…).
    plan_mode: bool = True


#: The chat profile's tools: read the user's own knowledge (vault, files in the
#: allowed folders, apps), read the public web, and act through declared app
#: verbs behind the per-tool consent gate. Three of them are handed over in
#: NARROWED form (``NARROWED``, built in emptyos/sdk/agent_tools/restricted.py):
#: hiding a schema does not bound what an offered tool can reach, and the stock
#: Fetch / CallApp / Read could reach a shell, any app method, and the daemon's
#: credentials. No shell, no code execution, no file writes, no daemon control.
#: ``CreateArtifact`` is the one write here, and it is a narrow one: it hands
#: HTML to viz and can choose nothing about where it lands (B4).
#: MCP connector tools are opted in per chat, never by default.
CHAT_TOOLS = frozenset({
    "VaultQuery",
    "Locate",
    "Read",
    "WebSearch",
    "Fetch",
    "CallApp",
    "Skill",
    "ContextRef",
    "CreateArtifact",
})

#: Tools a restricted profile receives only in narrowed form — the agent app
#: builds those instances (emptyos/sdk/agent_tools/restricted.py) and passes
#: them as ``narrowed``; the stock instance is never offered.
NARROWED = frozenset({"Fetch", "CallApp", "Read"})

PROFILES: dict[str, Profile] = {
    "coding": Profile(
        name="coding", tools=None, orient=True, scaffold=True,
        auto_skill=True, episodic=True, system_prompt_key="",
    ),
    "chat": Profile(
        name="chat", tools=CHAT_TOOLS, orient=False, scaffold=False,
        auto_skill=False, episodic=False, system_prompt_key="chat_system",
        plan_mode=False,
    ),
}

DEFAULT_PROFILE = "coding"


def profile_for(value) -> Profile | None:
    """The profile a stored session value names: blank → coding (every session
    that predates profiles); unknown → ``None``. An unknown value is refused
    rather than read as coding, because coding is the MOST permissive profile —
    a renamed or corrupted value must not hand a chat the shell."""
    v = str(value or "").strip()
    return PROFILES[DEFAULT_PROFILE] if not v else PROFILES.get(v)


def valid_profile(value) -> str | None:
    """API-boundary check: the canonical name, or ``None`` when not a profile.
    Blank is valid and means the default."""
    v = str(value or "").strip()
    if not v:
        return DEFAULT_PROFILE
    return v if v in PROFILES else None


#: Stored message kinds any provider can replay: native turns are stored as
#: flat {role, content: str} pairs, and a handoff message is a plain string.
PORTABLE_KINDS = frozenset({"native", "handoff", ""})


def history_kind_of(messages: list) -> str:
    """The wire kind a conversation's stored history is locked to — the
    ``provider_kind`` of its most recent non-portable message — or ``""`` when
    every message is portable (or there are none)."""
    for m in reversed(messages or []):
        kind = (m or {}).get("provider_kind", "") if isinstance(m, dict) else ""
        if kind not in PORTABLE_KINDS:
            return kind
    return ""


def mcp_server_of(tool_name: str) -> str:
    """``mcp__<server>__<tool>`` → ``<server>``; ``""`` for a native tool."""
    if not tool_name.startswith(MCP_PREFIX):
        return ""
    rest = tool_name[len(MCP_PREFIX):]
    server, sep, _tool = rest.partition("__")
    return server if sep else ""


def parse_connectors(value) -> list[str]:
    """A session's stored connector list (comma-separated) → server names."""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or "").split(",")
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def select_session_tools(registry: dict, profile: Profile, connectors=(), narrowed=None) -> dict:
    """The tool registry a turn of this profile may use.

    Coding returns the registry object itself — the exact pre-profile call.
    A restricted profile keeps its named tools plus the MCP tools of the
    connectors this session enabled; every other tool is simply not offered,
    so the model never sees a schema it may not call. A name in ``NARROWED``
    is taken from ``narrowed`` (``{name: tool}``) and dropped if absent there —
    a restricted profile never falls back to the unrestricted instance.
    """
    if profile.tools is None:
        return registry
    narrowed = narrowed or {}
    enabled = set(parse_connectors(connectors))
    out = {}
    for name, tool in registry.items():
        server = mcp_server_of(name)
        if name in profile.tools:
            if name in NARROWED:
                if name in narrowed:
                    out[name] = narrowed[name]
            else:
                out[name] = tool
        elif server and server in enabled:
            out[name] = tool
    return out
