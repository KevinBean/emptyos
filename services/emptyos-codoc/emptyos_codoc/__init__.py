"""emptyos-codoc — the live real-time collaboration service (Lane 1, no vault)."""

from .app import create_app
from .client import CoDocClient
from .store import CoDocStore

__all__ = ["create_app", "CoDocStore", "CoDocClient"]
