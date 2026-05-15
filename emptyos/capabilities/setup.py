"""Build capabilities from config. This is where config turns into live providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.capabilities import CapabilityRegistry
from emptyos.capabilities.providers.filesystem import (
    FilesystemReadProvider,
    FilesystemWriteProvider,
)
from emptyos.capabilities.providers.grep_search import GrepSearchProvider
from emptyos.capabilities.providers.human import (
    HumanReadProvider,
    HumanSearchProvider,
    HumanSeeProvider,
    HumanThinkProvider,
    HumanWriteProvider,
)
from emptyos.capabilities.types import (
    AnimateCapability,
    BrowseCapability,
    DrawCapability,
    ListenCapability,
    PronounceCapability,
    ReadCapability,
    SearchCapability,
    SeeCapability,
    SpeakCapability,
    ThinkCapability,
    WriteCapability,
)

if TYPE_CHECKING:
    from emptyos.kernel.config import Config


def build_capabilities(config: Config, settings=None, kernel=None) -> CapabilityRegistry:
    """Build all capabilities from config. Human providers are always last (fallback).

    `settings` is the optional SettingsService; when present, per-provider overrides
    like `think.<name>.model` are applied on top of emptyos.toml values so users can
    change their default model via the Settings app without editing config files.
    """
    registry = CapabilityRegistry()
    # Use the resolved absolute path from Config.notes_path so providers and
    # callers (apps that build paths from `vault_config_path`) agree — relative
    # paths in emptyos.toml otherwise lead to double-prefix bugs when an
    # already-vault-rooted path is fed back through the read/write providers.
    notes_path = str(config.notes_path) if config.notes_path else ""

    # --- Think (with domain routing) ---
    think = ThinkCapability()

    # Default provider chain
    think_config = config.get_section("capabilities.think")
    provider_names = think_config.get("providers", [])

    # Legacy [llm] config support
    if not provider_names:
        legacy_providers = config.get_section("llm.providers")
        if legacy_providers:
            provider_names = list(legacy_providers.keys())

    global_timeout = int(think_config.get("timeout", 0))

    for name in provider_names:
        provider = _build_think_provider(
            name, config, global_timeout=global_timeout, settings=settings
        )
        if provider:
            think.add_provider(provider)

    # Domain-specific provider chains
    domains_config = config.get_section("capabilities.think.domains")
    for domain_name, domain_cfg in domains_config.items():
        if not isinstance(domain_cfg, dict):
            continue
        domain_providers = []
        for pname in domain_cfg.get("providers", []):
            p = _build_think_provider(
                pname, config, model_override=domain_cfg.get("model"), settings=settings
            )
            if p:
                domain_providers.append(p)
        if domain_providers:
            think.add_domain(domain_name, domain_providers)

    # Bucket-specific provider chains — keyed by "domain/task_shape".
    # Config shape: [capabilities.think.buckets."text/classify"] providers = [...]
    buckets_config = config.get_section("capabilities.think.buckets")
    for bucket_name, bucket_cfg in buckets_config.items():
        if not isinstance(bucket_cfg, dict):
            continue
        bucket_providers = []
        for pname in bucket_cfg.get("providers", []):
            p = _build_think_provider(
                pname, config, model_override=bucket_cfg.get("model"), settings=settings
            )
            if p:
                bucket_providers.append(p)
        if bucket_providers:
            think.add_bucket(bucket_name, bucket_providers)

    think.add_provider(HumanThinkProvider())  # human is always last
    registry.register("think", think)

    # --- Read ---
    read = ReadCapability()
    read.add_provider(FilesystemReadProvider(base_path=notes_path))
    read.add_provider(HumanReadProvider())
    registry.register("read", read)

    # --- Write ---
    write = WriteCapability()
    write.add_provider(FilesystemWriteProvider(base_path=notes_path))
    write.add_provider(HumanWriteProvider())
    registry.register("write", write)

    # --- Search ---
    search = SearchCapability()
    search.add_provider(GrepSearchProvider(base_path=notes_path))
    search.add_provider(HumanSearchProvider())
    registry.register("search", search)

    # --- Speak (TTS) — baseline cloud providers from config, plugins append local engines ---
    speak = SpeakCapability()
    _register_openai_speak(speak, config)
    registry.register("speak", speak)

    # --- Listen (STT) — baseline cloud providers from config, plugins append local engines ---
    listen = ListenCapability()
    _register_openai_listen(listen, config)
    if kernel is not None:
        _register_browser_listen(listen, kernel, config)
    registry.register("listen", listen)

    # --- Pronounce (phoneme scoring) — providers added by plugins/pronounce.
    # Registered with an empty chain so apps can call `pronounce()` and get a
    # clean "no provider available" error when the plugin is offline, rather
    # than a KeyError. No human fallback: a human can't score per-phone
    # accuracy by ear, and the app should surface "scoring offline" upstream.
    pronounce = PronounceCapability()
    registry.register("pronounce", pronounce)

    # --- Draw (image generation) — providers added by plugins ---
    draw = DrawCapability()
    registry.register("draw", draw)

    # --- Animate (image-to-video / text-to-video) — providers added by plugins ---
    animate = AnimateCapability()
    registry.register("animate", animate)

    # --- See (camera capture) — providers added by plugins (webcam, etc.) ---
    # see is an input modality (like read), so a human handing over a file is a
    # legitimate final fallback per Dev Rule #2 — unlike generative modalities
    # (speak/draw) where a human can't fulfil the call.
    see = SeeCapability()
    if kernel is not None:
        _register_browser_see(see, kernel, config)
    see.add_provider(HumanSeeProvider())
    registry.register("see", see)

    # --- Browse (headless browser automation) — providers added by plugins
    # (playwright, etc.). No human fallback: a human can't fulfil a
    # `click("#submit")` call in any meaningful way, so the capability simply
    # raises when no provider is wired — the app should detect that and skip.
    browse = BrowseCapability()
    registry.register("browse", browse)

    return registry


def _register_openai_speak(speak, config: Config):
    """Register the OpenAI TTS provider when configured or OPENAI_API_KEY is set."""
    section = (
        config.get_section("capabilities.speak.openai-tts")
        or config.get_section("capabilities.speak.openai")
        or {}
    )
    enabled = section.get("enabled", True)
    if not enabled:
        return
    import os

    api_key_env = section.get("api_key_env", "OPENAI_API_KEY")
    # Register only when it could plausibly work — avoids a dead provider in the chain.
    if not (section or os.environ.get(api_key_env)):
        return
    from emptyos.capabilities.providers.openai_tts import OpenAITTSProvider

    speak.add_provider(
        OpenAITTSProvider(
            host=section.get("host", "https://api.openai.com"),
            model=section.get("model", "tts-1"),
            voice=section.get("voice", "alloy"),
            api_key_env=api_key_env,
            timeout=int(section.get("timeout", 30)),
        )
    )


def _register_openai_listen(listen, config: Config):
    """Register the OpenAI Whisper STT provider when configured or OPENAI_API_KEY is set."""
    section = (
        config.get_section("capabilities.listen.openai-whisper")
        or config.get_section("capabilities.listen.openai")
        or {}
    )
    enabled = section.get("enabled", True)
    if not enabled:
        return
    import os

    api_key_env = section.get("api_key_env", "OPENAI_API_KEY")
    if not (section or os.environ.get(api_key_env)):
        return
    from emptyos.capabilities.providers.openai_tts import OpenAIWhisperSTTProvider

    listen.add_provider(
        OpenAIWhisperSTTProvider(
            host=section.get("host", "https://api.openai.com"),
            model=section.get("model", "whisper-1"),
            api_key_env=api_key_env,
            timeout=int(section.get("timeout", 60)),
        )
    )


def _register_browser_listen(listen, kernel, config: Config):
    """Register the browser-side Web Speech API STT provider.

    Free, no API key, no GPU. Captures via the visitor's browser. Available
    only when at least one browser tab is connected to /ws — falls through
    cleanly when not (e.g. CLI invocations, headless tests).
    """
    section = config.get_section("capabilities.listen.browser-speech") or {}
    if section.get("enabled", True) is False:
        return
    from emptyos.capabilities.providers.browser import BrowserListenProvider

    listen.add_provider(
        BrowserListenProvider(
            kernel,
            default_lang=section.get("language", "en-US"),
            default_timeout=float(section.get("timeout", 30)),
        )
    )


def _register_browser_see(see, kernel, config: Config):
    """Register the browser-side getUserMedia camera-snapshot provider.

    Returns a base64 data URL of the frame. Apps that need raw bytes can
    decode via emptyos.sdk.utils.parse_data_url.
    """
    section = config.get_section("capabilities.see.browser-webcam") or {}
    if section.get("enabled", True) is False:
        return
    from emptyos.capabilities.providers.browser import BrowserSeeProvider

    see.add_provider(
        BrowserSeeProvider(
            kernel,
            default_timeout=float(section.get("timeout", 30)),
        )
    )


def _build_think_provider(
    name: str,
    config: Config,
    model_override: str | None = None,
    global_timeout: int = 0,
    settings=None,
):
    """Build a think provider from config by name.

    Model resolution order (highest → lowest):
    1. `model_override` argument (domain/bucket-specific override)
    2. `settings.get("think.<name>.model")` (user setting, persisted to data/settings.json)
    3. `section["model"]` (emptyos.toml)
    """
    section = config.get_section(f"capabilities.think.{name}")
    if not section:
        section = config.get_section(f"llm.providers.{name}")

    if not section:
        # Claude CLI needs no config section
        if name == "claude":
            section = {}
        else:
            return None

    from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider

    host = section.get("host", "")

    # Settings overlay: user-configured model beats emptyos.toml, but a domain/bucket
    # override still wins so per-task routing stays authoritative.
    settings_model = None
    if settings is not None:
        try:
            settings_model = settings.get(f"think.{name}.model")
        except Exception:
            settings_model = None
    model = model_override or settings_model or section.get("model", "")
    method = section.get("method", "")

    # Per-provider timeout overrides global timeout
    timeout = int(section.get("timeout", 0)) or global_timeout

    # Ollama
    if name == "ollama" or (host and "11434" in host):
        return OpenAICompatThinkProvider(
            host=host or "http://localhost:11434",
            model=model or "llama3.1",
            api_key_env="",
            provider_name="ollama",
            timeout=timeout,
        )

    # OpenAI (detect by either the literal name "openai", any section whose
    # name starts with "openai-" — e.g. `[capabilities.think.openai-full]` for
    # a second instance using a different model — or any host pointing at
    # api.openai.com).
    if name == "openai" or name.startswith("openai-") or "openai.com" in (host or ""):
        return OpenAICompatThinkProvider(
            host=host or "https://api.openai.com",
            model=model or "gpt-5-mini",
            api_key_env=section.get("api_key_env", "OPENAI_API_KEY"),
            # Use the config section name so multiple OpenAI instances (mini vs
            # full vs nano) each get a unique `p.name` and the agent's
            # `_resolve_provider(name)` can pick the right one via `/model`.
            provider_name=name,
            timeout=timeout,
        )

    # Claude via CLI (free with Max subscription)
    if name == "claude" and method != "api":
        from emptyos.capabilities.providers.claude_cli import ClaudeCLIThinkProvider

        vault_path = config.get("notes.path", "")
        network_port = int(config.get("network.port", 9000))
        return ClaudeCLIThinkProvider(
            model=model,
            max_tokens=int(section.get("max_tokens", 4096)),
            timeout=timeout,
            cwd=vault_path,
            effort=section.get("effort", "low"),
            mcp_enabled=bool(section.get("mcp_enabled", False)),
            mcp_port=network_port,
        )

    # Claude via API (paid per token)
    if name == "claude" and method == "api":
        return OpenAICompatThinkProvider(
            host=host or "https://api.anthropic.com",
            model=model or "claude-sonnet-4-20250514",
            api_key_env=section.get("api_key_env", "ANTHROPIC_API_KEY"),
            provider_name="claude",
        )

    # Anthropic SDK — native tool_use blocks, streaming, prompt caching
    if name == "anthropic" or name == "anthropic_sdk":
        from emptyos.capabilities.providers.anthropic_sdk import AnthropicSDKProvider

        return AnthropicSDKProvider(
            model=model or "claude-sonnet-4-5-20250929",
            api_key_env=section.get("api_key_env", "ANTHROPIC_API_KEY"),
            max_tokens=int(section.get("max_tokens", 8192)),
            timeout=timeout or 120,
            cache_system=bool(section.get("cache_system", True)),
        )

    # Generic OpenAI-compatible endpoint
    if host:
        return OpenAICompatThinkProvider(
            host=host,
            model=model or "default",
            api_key_env=section.get("api_key_env", ""),
            provider_name=name,
        )

    return None
