"""Helpers for app CLI command argument binding.

App CLI commands are registered as a catch-all ``args: list[str]`` Typer
argument, then dispatched either locally or through ``/api/cli``. Keep the
binding rules in one place so terminal and daemon dispatch behave the same.
"""

from __future__ import annotations

import inspect
from typing import Any


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
