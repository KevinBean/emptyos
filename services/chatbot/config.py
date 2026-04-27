"""Load sites.toml — per-site config + defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:
    import tomli as tomllib  # type: ignore


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
        )

    return Config(defaults=defaults, sites=sites)
