"""Decorators for app methods — @cli_command, @web_route, @on_event, @scheduled."""

from __future__ import annotations

from typing import Any, Callable


def cli_command(name: str, help: str = ""):
    """Register a method as a CLI subcommand."""

    def decorator(func: Callable) -> Callable:
        func._eos_cli = {"name": name, "help": help}
        return func

    return decorator


def web_route(method: str, path: str):
    """Register a method as a web route."""

    def decorator(func: Callable) -> Callable:
        func._eos_web = {"method": method, "path": path}
        return func

    return decorator


def on_event(event_type: str):
    """Register a method as an event handler."""

    def decorator(func: Callable) -> Callable:
        func._eos_event = {"type": event_type}
        return func

    return decorator


def ws_route(path: str):
    """Register a method as a WebSocket endpoint."""

    def decorator(func: Callable) -> Callable:
        func._eos_ws = {"path": path}
        return func

    return decorator


def scheduled(cron: str, id: str = ""):
    """Register a method as a scheduled job."""

    def decorator(func: Callable) -> Callable:
        func._eos_scheduled = {"cron": cron, "id": id or func.__name__}
        return func

    return decorator
