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
        """Raise or return the value. Called after coerce.

        Default body (no ``validate_fn``): a generic required/pattern check
        driven by ``col_config`` — ``required: bool`` and ``pattern: str`` (a
        regex the string form of ``value`` must fullmatch). Nothing calls
        ``validate()`` today (boards ignores it), so this is purely additive;
        ``emptyos.sdk.collection_app.CollectionLibrary`` is the first caller.
        """
        if self.validate_fn:
            return self.validate_fn(value, col_config)
        label = col_config.get("label") or col_config.get("id") or "field"
        if col_config.get("required") and value in (None, "", []):
            raise ValueError(f"{label} is required")
        pattern = col_config.get("pattern")
        if pattern and value not in (None, ""):
            import re

            if not re.fullmatch(pattern, str(value)):
                raise ValueError(f"{label} does not match the required pattern")
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


def columns_to_form_fields(columns: list[dict]) -> list[dict]:
    """Map a list of column configs (a manifest `[collection]` schema, or a
    board's own `columns`) into the `{key,label,type,options,required,value}`
    shape `EOS_UI.formHtml`/`formValues` (eos-components.js) actually read.

    Second consumer: `emptyos.web.auto_ui._schema_fields_for_collection`
    (the authenticated add-form) and `apps.public.standard.boards.
    public_form._public_form_fields` (the anonymous public form) both need
    this exact mapping — extracted here rather than one importing the
    other's private helper (CLAUDE.md rule 9: build specific first, extract
    on the second real caller).
    """
    out = []
    for c in columns:
        field_id = c.get("id", "")
        field: dict = {
            "key": field_id,
            "label": c.get("label") or field_id.replace("_", " ").title(),
            "type": c.get("type", "text"),
            "options": c.get("options"),
            "required": bool(c.get("required")),
            "value": c.get("default", ""),
        }
        if c.get("type") == "link-record":
            # No local id sample to preload — the picker fetches its own
            # option list client-side (EOS_UI.formHtml, eos-components.js).
            field["target_board"] = c.get("target_board", "")
            field["multi"] = bool(c.get("multi"))
        out.append(field)
    return out


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
_register(ColumnType("checkbox", storage=bool, widget="checkbox", groupable=True))
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

def _coerce_link_record(value: Any, col_config: dict) -> list[str]:
    """Normalize loose link-record input into a clean list of target item ids.

    Before this existed, coerce() was pure pass-through (the dataclass default)
    for every caller — `CollectionLibrary.coerce_and_validate` would store
    whatever raw shape a client POSTed (a bare string, `None`, junk) directly
    into frontmatter with no relation to `storage=list`. Accepts: a list of
    ids (already-shaped UI transport), a single id string, or `None`/empty.
    `multi=false` truncates to at most one id, matching the "stored as a
    1-element list for shape uniformity" convention documented above.
    """
    if value is None or value == "":
        ids: list[str] = []
    elif isinstance(value, (list, tuple)):
        ids = [str(v).strip() for v in value if str(v).strip()]
    else:
        ids = [str(value).strip()] if str(value).strip() else []
    if not col_config.get("multi") and len(ids) > 1:
        ids = ids[:1]
    return ids


# link-record — typed reference to items on another board. Stores a list of
# item IDs. `multi=false` means single-target (stored as a 1-element list for
# shape uniformity). `target_board` + optional `inverse` are per-column config.
# Distinct from the `link` type (URL).
_register(
    ColumnType(
        "link-record", storage=list, list_like=True, widget="record-picker",
        groupable=True, coerce_fn=_coerce_link_record,
    )
)


def _reject_rollup_write(value: Any, col_config: dict) -> Any:
    """A rollup is always computed (board_engine.evaluate_formulas re-derives
    it on every read) — never a value a caller sets directly. Without this,
    `CollectionLibrary.coerce_and_validate`'s generic validate() would happily
    accept and store whatever a POST/PUT sent, which then silently vanishes
    the next time the item is read (evaluate_formulas overwrites it) — a
    confusing "my edit didn't stick" bug with no error to explain it. Raising
    here instead gives the caller an actual reason at write time.
    """
    label = col_config.get("label") or col_config.get("id") or "field"
    raise ValueError(f"{label} is a computed field — cannot be set directly")


# rollup — aggregate a field across the records a link-record column points to
# (Notion/Airtable rollup). Structured config instead of a raw formula string:
#   {source_link: "<link-record col id>", target_field: "<field>", agg: "<fn>"}
# where agg ∈ sum/avg/count/min/max/earliest/latest/count_unique/percent_checked/
# show. board_engine compiles it to the equivalent formula expression and runs
# it through the same evaluator — read-only computed value, like `formula`.
_register(ColumnType("rollup", storage=str, widget="formula", groupable=True, validate_fn=_reject_rollup_write))


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
