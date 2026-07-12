"""Column-type registry — typed record columns shared across apps.

Boards is the first consumer, but the registry is app-agnostic. Any app that
stores typed records (CRM contacts, finance entries, practice sessions with
typed columns) can register its own types and pick up the shared ones for free.

A ColumnType describes: storage shape (Python type), coercion on write,
validation, and a render hint (widget name + config) consumed by frontends.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ColumnType:
    """Describes one column kind. Subclass or instantiate with overrides."""

    id: str
    storage: type = str  # Python type for VaultLibrary round-trip
    person_like: bool = False  # emits assignment deltas on change
    list_like: bool = False  # multi-value (list of ids / tags)
    role: str | None = None  # emit_assignment role (designer/checker/...)
    default: Any = None
    widget: str = "text"  # frontend renderer/editor key
    groupable: bool = False  # makes sense as a group-by axis in board views
    # Optional per-type hooks. Default impls are pass-through.
    coerce_fn: Callable[[Any, dict], Any] | None = None
    validate_fn: Callable[[Any, dict], Any] | None = None

    def coerce(self, value: Any, col_config: dict) -> Any:
        """Called on write. Turn loose input into storage-shaped value."""
        if self.coerce_fn:
            return self.coerce_fn(value, col_config)
        return value

    def validate(self, value: Any, col_config: dict) -> Any:
        """Raise or return the value. Called after coerce."""
        if self.validate_fn:
            return self.validate_fn(value, col_config)
        return value

    def render_hint(self, col_config: dict) -> dict:
        """Metadata the frontend uses to pick a renderer/editor."""
        return {"widget": self.widget}


class ColumnTypeRegistry:
    """Process-wide registry. Apps register at import time."""

    _types: dict[str, ColumnType] = {}

    @classmethod
    def register(cls, t: ColumnType) -> ColumnType:
        cls._types[t.id] = t
        return t

    @classmethod
    def get(cls, type_id: str) -> ColumnType:
        """Return the type or fall back to `text`. Never raises on unknown."""
        return cls._types.get(type_id) or cls._types["text"]

    @classmethod
    def has(cls, type_id: str) -> bool:
        return type_id in cls._types

    @classmethod
    def all(cls) -> dict[str, ColumnType]:
        return dict(cls._types)

    @classmethod
    def ids_where(cls, **predicates) -> tuple[str, ...]:
        """Return ids of types matching every (attr, value) predicate.
        Example: ColumnTypeRegistry.ids_where(person_like=True)."""
        out = []
        for tid, t in cls._types.items():
            if all(getattr(t, k, None) == v for k, v in predicates.items()):
                out.append(tid)
        return tuple(out)


# ── Built-in types — matches apps/boards/board_engine.py _TYPE_MAP exactly ──

_register = ColumnTypeRegistry.register

_register(ColumnType("text", storage=str, widget="text", groupable=True))
_register(
    ColumnType("number", storage=float, widget="number")
)  # continuous — not useful as group axis
_register(ColumnType("select", storage=str, widget="select", groupable=True))
_register(
    ColumnType("multi-select", storage=list, list_like=True, widget="multi-select", groupable=True)
)
_register(ColumnType("date", storage=str, widget="date", groupable=True))
_register(ColumnType("checkbox", storage=str, widget="checkbox", groupable=True))
_register(ColumnType("link", storage=str, widget="url"))  # URL — every value unique
_register(
    ColumnType("formula", storage=str, widget="formula", groupable=True)
)  # often yields a small set (badges)
_register(ColumnType("timeline", storage=str, widget="timeline"))

# Person-family — always useful as group axis ("group by assignee").
_register(ColumnType("person", storage=str, person_like=True, widget="person", groupable=True))
_register(
    ColumnType(
        "multi-person",
        storage=list,
        person_like=True,
        list_like=True,
        widget="multi-person",
        groupable=True,
    )
)
_register(
    ColumnType(
        "designer", storage=str, person_like=True, role="designer", widget="person", groupable=True
    )
)
_register(
    ColumnType(
        "checker", storage=str, person_like=True, role="checker", widget="person", groupable=True
    )
)
_register(
    ColumnType(
        "approver", storage=str, person_like=True, role="approver", widget="person", groupable=True
    )
)
_register(
    ColumnType(
        "reviewer", storage=str, person_like=True, role="reviewer", widget="person", groupable=True
    )
)

# List-shaped domain types (boards uses these for skills & dependencies today).
_register(ColumnType("skills", storage=list, list_like=True, widget="tags", groupable=True))
_register(ColumnType("dependencies", storage=list, list_like=True, widget="dependencies"))

# link-record — typed reference to items on another board. Stores a list of
# item IDs. `multi=false` means single-target (stored as a 1-element list for
# shape uniformity). `target_board` + optional `inverse` are per-column config.
# Distinct from the `link` type (URL).
_register(
    ColumnType("link-record", storage=list, list_like=True, widget="record-picker", groupable=True)
)

# rollup — aggregate a field across the records a link-record column points to
# (Notion/Airtable rollup). Structured config instead of a raw formula string:
#   {source_link: "<link-record col id>", target_field: "<field>", agg: "<fn>"}
# where agg ∈ sum/avg/count/min/max/earliest/latest/count_unique/percent_checked/
# show. board_engine compiles it to the equivalent formula expression and runs
# it through the same evaluator — read-only computed value, like `formula`.
_register(ColumnType("rollup", storage=str, widget="formula", groupable=True))


def _coerce_checklist(value: Any, col_config: dict) -> Any:
    """Normalize checklist input to a JSON-encoded string of [{text, done}].

    Vault frontmatter is flat-only (CLAUDE.md gotcha), so the storage shape is
    a JSON string. Accepts: a list of {text, done} dicts (UI transport), a
    list of plain strings (all not-done), or an already-encoded string.
    """
    import json

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        items = []
        for it in value:
            if isinstance(it, dict):
                items.append({"text": str(it.get("text", "")), "done": bool(it.get("done"))})
            else:
                items.append({"text": str(it), "done": False})
        return json.dumps(items, ensure_ascii=False)
    return "[]"


# checklist — subtasks inside a record ([{text, done}]), stored as a JSON
# string for flat-frontmatter compatibility; app-sourced boards pass the list
# through set_field JSON-natively.
_register(ColumnType("checklist", storage=str, widget="checklist", coerce_fn=_coerce_checklist))

# attachment — display-only 📎N column backed by the boards attachments
# directory index; never round-trips through set_field.
_register(ColumnType("attachment", storage=list, list_like=True, widget="attachment"))


__all__ = ["ColumnType", "ColumnTypeRegistry"]
