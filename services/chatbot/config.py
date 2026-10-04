"""Load sites.toml — per-site config + defaults.

Editable fields (mutable via /admin/sites/{id}):
  - model, persona, daily_cap_usd, starter_questions
File-only fields (require SSH + manual edit):
  - name, allowed_origins, corpus_url
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:
    import tomli as tomllib  # type: ignore


# Fields that the publish app may overwrite via /admin/sites/{id}.
# Keeping this small + explicit prevents the API from mutating security-
# sensitive fields like allowed_origins or the corpus URL.
SYNCED_FIELDS = {"model", "persona", "daily_cap_usd", "starter_questions", "avoid_topics"}
STUDIO_FIELDS = {
    "name", "allowed_origins", "corpus_url", "knowledge_sources", "catalog_url",
    "catalog_stale_seconds", "commerce_enabled", "allowed_actions", "action_endpoint",
    "helpdesk_endpoint", "model", "persona", "daily_cap_usd", "starter_questions",
    "enabled", "brand_color", "welcome_message", "avoid_topics", "lead_capture_enabled",
}


@dataclass
class SiteConfig:
    id: str
    name: str
    allowed_origins: list[str]
    corpus_url: str
    daily_cap_usd: float
    model: str
    persona: str
    starter_questions: list[str] = field(default_factory=list)
    managed_by: str = "publish"
    enabled: bool = True
    knowledge_sources: list[str] = field(default_factory=list)
    catalog_url: str = ""
    catalog_stale_seconds: int = 3600
    commerce_enabled: bool = False
    allowed_actions: list[str] = field(default_factory=list)
    action_endpoint: str = ""
    helpdesk_endpoint: str = ""
    brand_color: str = "#7c5cff"
    welcome_message: str = "How can I help?"
    # Retired/unavailable products or pages the model must never recommend.
    # Second deprecation lever — pairs with removing the page from the corpus
    # (`corpus: false` frontmatter in publish). Empty = prompt unchanged.
    avoid_topics: list[str] = field(default_factory=list)
    # Content-site lead capture ("leave your details" form). Off by default;
    # commerce sites already have handoff.create.
    lead_capture_enabled: bool = False


@dataclass
class Defaults:
    daily_cap_usd: float = 2.0
    global_cap_usd: float = 10.0
    model: str = "gpt-5-nano"
    max_output_tokens: int = 300
    max_input_chars: int = 4000
    rate_limit_per_hour: int = 20
    rate_limit_per_day: int = 60
    corpus_ttl_seconds: int = 3600
    provider: str = "openai"


@dataclass
class Config:
    defaults: Defaults
    sites: dict[str, SiteConfig]


def load_config(path: str | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("CHATBOT_SITES_PATH", "./sites.toml"))
    if not cfg_path.exists():
        raise FileNotFoundError(f"sites.toml not found at {cfg_path}")
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)

    d = raw.get("defaults", {})
    defaults = Defaults(
        daily_cap_usd=float(d.get("daily_cap_usd", 2.0)),
        global_cap_usd=float(d.get("global_cap_usd", 10.0)),
        model=str(d.get("model", "gpt-5-nano")),
        max_output_tokens=int(d.get("max_output_tokens", 300)),
        max_input_chars=int(d.get("max_input_chars", 4000)),
        rate_limit_per_hour=int(d.get("rate_limit_per_hour", 20)),
        rate_limit_per_day=int(d.get("rate_limit_per_day", 60)),
        corpus_ttl_seconds=int(d.get("corpus_ttl_seconds", 3600)),
        provider=str(d.get("provider", "openai")),
    )

    sites: dict[str, SiteConfig] = {}
    for site_id, sd in (raw.get("sites") or {}).items():
        if not isinstance(sd, dict):
            continue
        sites[site_id] = SiteConfig(
            id=site_id,
            name=str(sd.get("name", site_id)),
            allowed_origins=list(sd.get("allowed_origins", [])),
            corpus_url=str(sd.get("corpus_url", "")),
            daily_cap_usd=float(sd.get("daily_cap_usd", defaults.daily_cap_usd)),
            model=str(sd.get("model", defaults.model)),
            persona=str(sd.get("persona", "")),
            starter_questions=list(sd.get("starter_questions", [])),
            managed_by=str(sd.get("managed_by", "publish")),
            enabled=bool(sd.get("enabled", True)),
            knowledge_sources=list(sd.get("knowledge_sources", [])),
            catalog_url=str(sd.get("catalog_url", "")),
            catalog_stale_seconds=int(sd.get("catalog_stale_seconds", 3600)),
            commerce_enabled=bool(sd.get("commerce_enabled", False)),
            allowed_actions=list(sd.get("allowed_actions", [])),
            action_endpoint=str(sd.get("action_endpoint", "")),
            helpdesk_endpoint=str(sd.get("helpdesk_endpoint", "")),
            brand_color=str(sd.get("brand_color", "#7c5cff")),
            welcome_message=str(sd.get("welcome_message", "How can I help?")),
            avoid_topics=list(sd.get("avoid_topics", [])),
            lead_capture_enabled=bool(sd.get("lead_capture_enabled", False)),
        )

    return Config(defaults=defaults, sites=sites)


# ── Atomic sites.toml rewrite (used by /admin/sites/{id}) ────────────


def _toml_escape_basic(s: str) -> str:
    """Escape a string for inclusion in a TOML basic-string ("..."). Escapes
    backslashes, double-quotes, and control chars."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_multiline(s: str) -> str:
    """Render a multi-line string as a TOML triple-quoted basic string.
    Escape backslashes + triple-quote sequences only."""
    body = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""\n{body}\n"""'


def _render_string(s: str) -> str:
    if "\n" in s or len(s) > 80:
        return _toml_multiline(s)
    return _toml_escape_basic(s)


def _render_list(items: list) -> str:
    if not items:
        return "[]"
    parts = [_toml_escape_basic(str(x)) for x in items]
    return "[\n  " + ",\n  ".join(parts) + ",\n]"


def render_config_toml(cfg: Config) -> str:
    """Render Config back to TOML. Auto-managed format; loses comments.

    Use only via /admin/sites/{id} — for hand-edited fields
    (allowed_origins, corpus_url) the operator's last-saved file is the
    source of truth and gets re-rendered here verbatim from the in-memory
    Config (which was loaded from that file moments earlier).
    """
    d = cfg.defaults
    lines = [
        "# sites.toml — managed by the chatbot service.",
        "# Editable via the publish app: model, persona, daily_cap_usd, starter_questions.",
        "# File-only (must edit here on the VPS): allowed_origins, corpus_url, name.",
        "",
        "[defaults]",
        f"daily_cap_usd = {d.daily_cap_usd}",
        f"global_cap_usd = {d.global_cap_usd}",
        f"model = {_toml_escape_basic(d.model)}",
        f"max_output_tokens = {d.max_output_tokens}",
        f"max_input_chars = {d.max_input_chars}",
        f"rate_limit_per_hour = {d.rate_limit_per_hour}",
        f"rate_limit_per_day = {d.rate_limit_per_day}",
        f"corpus_ttl_seconds = {d.corpus_ttl_seconds}",
        f"provider = {_toml_escape_basic(d.provider)}",
        "",
    ]
    for site_id in sorted(cfg.sites):
        s = cfg.sites[site_id]
        lines.append(f"[sites.{site_id}]")
        lines.append(f"name = {_toml_escape_basic(s.name)}")
        lines.append(f"allowed_origins = {_render_list(s.allowed_origins)}")
        lines.append(f"corpus_url = {_toml_escape_basic(s.corpus_url)}")
        lines.append(f"managed_by = {_toml_escape_basic(s.managed_by)}")
        lines.append(f"enabled = {'true' if s.enabled else 'false'}")
        if s.knowledge_sources:
            lines.append(f"knowledge_sources = {_render_list(s.knowledge_sources)}")
        if s.catalog_url:
            lines.append(f"catalog_url = {_toml_escape_basic(s.catalog_url)}")
        lines.append(f"catalog_stale_seconds = {s.catalog_stale_seconds}")
        lines.append(f"commerce_enabled = {'true' if s.commerce_enabled else 'false'}")
        if s.allowed_actions:
            lines.append(f"allowed_actions = {_render_list(s.allowed_actions)}")
        if s.action_endpoint:
            lines.append(f"action_endpoint = {_toml_escape_basic(s.action_endpoint)}")
        if s.helpdesk_endpoint:
            lines.append(f"helpdesk_endpoint = {_toml_escape_basic(s.helpdesk_endpoint)}")
        if s.lead_capture_enabled:
            lines.append("lead_capture_enabled = true")
        if s.avoid_topics:
            lines.append(f"avoid_topics = {_render_list(s.avoid_topics)}")
        lines.append(f"brand_color = {_toml_escape_basic(s.brand_color)}")
        lines.append(f"welcome_message = {_toml_escape_basic(s.welcome_message)}")
        lines.append(f"daily_cap_usd = {s.daily_cap_usd}")
        lines.append(f"model = {_toml_escape_basic(s.model)}")
        if s.persona:
            lines.append(f"persona = {_render_string(s.persona)}")
        if s.starter_questions:
            lines.append(f"starter_questions = {_render_list(s.starter_questions)}")
        lines.append("")
    return "\n".join(lines)


def write_config_atomic(cfg: Config, path: str | None = None) -> Path:
    """Write the Config to sites.toml atomically (tmp + rename) when possible,
    fall back to direct overwrite when not (Docker single-file bind mount).

    The deployment uses ``./sites.toml:/app/sites.toml`` in compose, which means
    ``/app/sites.toml`` is itself a mount point inside the container — Linux
    rejects rename-onto-mount-point with ``EBUSY``. In that case we just write
    the contents in-place. Loses atomicity (a crash mid-write could leave a
    truncated file), but the alternative is the API doesn't work at all.

    Returns the path written. Caller is responsible for reloading the in-memory
    Config (the writer doesn't replace the global CONFIG).
    """
    cfg_path = Path(path or os.environ.get("CHATBOT_SITES_PATH", "./sites.toml"))
    body = render_config_toml(cfg)

    # Try atomic rename first — works on a normal filesystem, fails on Docker
    # single-file bind mount with EBUSY.
    tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(cfg_path)
    except OSError as e:
        # Clean up tmp regardless of which step failed
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        # Bind-mounted single file: EBUSY (rename) or EROFS (read-only mount).
        # Fall back to direct overwrite.
        if e.errno in (16, 30):  # EBUSY, EROFS
            cfg_path.write_text(body, encoding="utf-8")
        else:
            raise
    return cfg_path
