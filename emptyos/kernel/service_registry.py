"""Service registry — dependency injection for EmptyOS services."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ServiceStatus(Enum):
    REGISTERED = "registered"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass
class ServiceEntry:
    name: str
    instance: Any
    tags: list[str] = field(default_factory=list)
    status: ServiceStatus = ServiceStatus.REGISTERED


class ServiceRegistry:
    """Named service container. Services register themselves, apps request them."""

    def __init__(self):
        self._services: dict[str, ServiceEntry] = {}

    def register(self, name: str, service: Any, tags: list[str] | None = None):
        """Register a service by name."""
        self._services[name] = ServiceEntry(
            name=name,
            instance=service,
            tags=tags or [],
            status=ServiceStatus.REGISTERED,
        )

    def get(self, name: str) -> Any:
        """Get a service by name. Raises KeyError if not found."""
        entry = self._services.get(name)
        if entry is None:
            raise KeyError(f"Service not found: {name}")
        return entry.instance

    def get_optional(self, name: str) -> Any | None:
        """Get a service by name, or None if not registered."""
        entry = self._services.get(name)
        return entry.instance if entry else None

    def has(self, name: str) -> bool:
        return name in self._services

    def list(self) -> list[ServiceEntry]:
        return list(self._services.values())
