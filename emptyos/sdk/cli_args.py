"""Helpers for app CLI command argument binding.

App CLI commands are registered as a catch-all ``args: list[str]`` Typer
argument, then dispatched either locally or through ``/api/cli``. Keep the
binding rules in one place so terminal and daemon dispatch behave the same.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Iterable


def resolve_cli_method(
    cli_methods: Iterable[tuple[dict, Any]],
    cmd_name: str,
    args: list[str] | None,
) -> tuple[Any | None, list[str]]:
    """Resolve an ``eos <app> [sub] [args…]`` invocation to (method, remaining_args).

    App CLI commands register the manifest command name as the top-level `eos`
    subcommand, but a method may be decorated ``@cli_command("<sub>")`` with a
    different name (the author's intended sub-verb, e.g. ``eos <cmd>
    <sub>``). Resolution order:

    1. **Default command** — a method whose name equals ``cmd_name`` (the
       common case; the app has one command matching its manifest entry).
    2. **Subcommand** — otherwise, if ``args[0]`` names a method, treat it as a
       sub-verb and consume it, passing the rest as that method's args.
    3. **Single-method fallback** — otherwise, if the app has exactly one CLI
       method, use it (the author named the method differently, e.g.
       ``cli_status``, but there is only one command it could mean). All args
       pass through as that method's args.

    Returns ``(None, args)`` when nothing matches (caller reports not-found).
    """
    pairs = [(meta["name"], m) for meta, m in cli_methods]
    by_name = dict(pairs)
    rest = list(args or [])
    if cmd_name in by_name:
        return by_name[cmd_name], rest
    if rest and rest[0] in by_name:
        return by_name[rest[0]], rest[1:]
    if len(pairs) == 1:
        return pairs[0][1], rest
    return None, rest


def render_cli_return(value: Any) -> str | None:
    """Render a CLI command's *return value* for display, or None to show nothing.

    The CLI harness captures printed stdout; a command that ``return``s a value
    instead of printing would otherwise show nothing. Render it: ``str`` as-is,
    ``dict``/``list`` as pretty JSON, ``None`` as nothing, anything else via
    ``str()``. A command that already printed (non-empty stdout) keeps its own
    output — the caller only renders the return when nothing was printed.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return str(value)


def _annotation_name(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", str(annotation))


def _coerce(value: Any, param: inspect.Parameter) -> Any:
    if not isinstance(value, str):
        return value
    ann = _annotation_name(param.annotation)
    if ann in ("int", "builtins.int"):
        return int(value)
    if ann in ("float", "builtins.float"):
        return float(value)
    if ann in ("bool", "builtins.bool"):
        return value.lower() in ("true", "1", "yes", "on")
    return value


def bind_cli_kwargs(method: Any, args: list[str] | None) -> dict[str, Any]:
    """Bind raw catch-all CLI args to a decorated app command's kwargs.

    Supports both positional args and ``key=value`` args. A leading ``--`` is
    accepted for convenience, so ``ttl_s=1800`` and ``--ttl-s=1800`` both map to
    a parameter named ``ttl_s``.
    """
    sig = inspect.signature(method)
    params = [
        p for p in sig.parameters.values()
        if p.name != "self"
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    var_kw = next(
        (p for p in sig.parameters.values() if p.kind == inspect.Parameter.VAR_KEYWORD),
        None,
    )
    by_name = {p.name: p for p in params}

    positional: list[str] = []
    named: dict[str, str] = {}
    extra_named: dict[str, str] = {}
    for raw in list(args or []):
        if isinstance(raw, str) and "=" in raw:
            key, value = raw.split("=", 1)
            key = key.strip().lstrip("-").replace("-", "_")
            if key in by_name:
                named[key] = value
                continue
            if var_kw is not None and key:
                extra_named[key] = value
                continue
        positional.append(raw)

    kwargs: dict[str, Any] = {}
    pos_i = 0
    for param in params:
        if param.name in named:
            kwargs[param.name] = _coerce(named[param.name], param)
        elif pos_i < len(positional):
            kwargs[param.name] = _coerce(positional[pos_i], param)
            pos_i += 1
        elif param.default is not inspect.Parameter.empty:
            kwargs[param.name] = param.default

    if var_kw is not None:
        kwargs.update(extra_named)
    return kwargs
