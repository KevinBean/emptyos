"""Board / view configuration schema.

The contract any app must speak when handing config to ``DynamicBoardLibrary``
or to the EOS_UI view helpers (``viewSwitcher``, ``kanbanLayout``,
``tableView``, ``inlineCellEdit``). Reference for both backend and frontend.

Shape (informal — Python TypedDicts below for IDE support)::

    {
        "id": "task-tracker",
        "name": "Tasks",
        "description": "Free-form description.",
        "source": {"type": "app", "app": "task", "method": "list_all"},
        "tags": ["board-config"],          # optional, for vault-tag sources
        "columns": [
            {
                "id": "status",                  # field key on the item
                "label": "Status",               # column header
                "type": "select",                # see COLUMN_TYPES
                "options": ["open", "done"],     # for select
                "color_map": {"open": "active", "done": "completed"},
                "prefix": "$", "suffix": "%",    # display modifiers
                "expression": "...",             # for type=formula
                "target_board": "deliverables",  # for type=link-record
                "multi": True, "inverse": "...",
            },
            ...
        ],
        "views": [
            {"type": "table", "default": True},
            {"type": "kanban", "group_by": "status"},
            {"type": "timeline", "start_field": "created", "end_field": "deadline"},
            {"type": "calendar", "date_field": "due"},
            {"type": "chart", "group_by": "status",
             "agg_field": "value", "agg_fn": "sum"},
        ],
        "kanban_group_by": "status",       # default kanban grouping
    }

The ``source`` block:

  * ``vault_tag`` — read vault notes by tag. ``{"type": "vault_tag",
    "tag": "song"}``.
  * ``app`` — call another app for its canonical list. The source app is the
    system of record; inline edits delegate to its ``set_field``. Shape:
    ``{"type": "app", "app": "<id>", "method": "list_all"}``.
  * ``mixed`` — union, with ``_source`` tag per row.

The ``color_map`` is name-only — values must map to existing
``.eos-badge-status-*`` / ``.eos-badge-priority-*`` / ``.eos-badge-age-*``
classes (see ``emptyos/web/static/eos-components.css``). Avoid raw hex
colours in board configs.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


COLUMN_TYPES: tuple[str, ...] = (
    "text", "number", "date", "boolean",
    "select", "multi-select",
    "url", "email",
    "person", "people",
    "tags", "rating",
    "formula", "link-record",
)

VIEW_TYPES: tuple[str, ...] = (
    "table", "kanban", "timeline", "calendar", "chart", "gallery",
)

SourceType = Literal["vault_tag", "app", "mixed"]


class VaultTagSource(TypedDict, total=False):
    type: Literal["vault_tag"]
    tag: str


class AppSource(TypedDict, total=False):
    type: Literal["app"]
    app: str
    method: str


class MixedSource(TypedDict, total=False):
    type: Literal["mixed"]
    sources: list[dict]


class ColumnConfig(TypedDict, total=False):
    id: str
    label: str
    type: str                     # one of COLUMN_TYPES
    options: list[str]            # for select / multi-select
    color_map: dict[str, str]     # value → eos-badge variant
    prefix: str
    suffix: str
    expression: str               # for type=formula
    target_board: str             # for type=link-record
    multi: bool
    inverse: str


class ViewConfig(TypedDict, total=False):
    type: str                     # one of VIEW_TYPES
    default: bool
    group_by: str
    start_field: str
    end_field: str
    date_field: str
    agg_field: str
    agg_fn: Literal["count", "sum", "avg"]


class BoardConfig(TypedDict, total=False):
    id: str
    name: str
    description: str
    source: dict                  # VaultTagSource | AppSource | MixedSource
    source_tag: str               # legacy fallback when ``source`` is absent
    tags: list[str]
    columns: list[ColumnConfig]
    views: list[ViewConfig]
    kanban_group_by: str


def normalize_source(config: BoardConfig | dict) -> dict[str, Any]:
    """Resolve the ``source`` block from a config, falling back to legacy
    ``source_tag``. Mirrors the logic inside ``DynamicBoardLibrary.__init__``
    so non-board callers can introspect the source without instantiating."""
    src = config.get("source")
    if src:
        return dict(src)
    return {"type": "vault_tag", "tag": config.get("source_tag", "")}


def default_view(config: BoardConfig | dict) -> str:
    """Return the type of the default view, or ``'table'`` as a safe fallback."""
    for v in config.get("views") or []:
        if v.get("default"):
            return v.get("type") or "table"
    views = config.get("views") or []
    if views:
        return views[0].get("type") or "table"
    return "table"
