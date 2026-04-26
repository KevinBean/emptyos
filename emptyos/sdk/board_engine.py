"""Board Engine — generic source/filter/aggregate engine.

Extracted from apps/boards so any app can own a "view-over-data" surface
(table / kanban / timeline / calendar / chart) without re-implementing the
source-resolution + filter + sort + aggregate machinery.

Three source types via a `source` dict on the board config:
  - ``{"type": "vault_tag", "tag": "..."}`` — read vault notes by tag.
  - ``{"type": "app", "app": "<id>", "method": "<list_all>"}`` — call another
    app via ``call_app``; the source app is the system of record. Inline edits
    delegate to ``call_app(app, "set_field", id=..., field=..., value=...)``.
  - ``{"type": "mixed", "sources": [...]}`` — union of the above with a
    ``_source`` key per row.

The engine is intentionally minimal. Boards-specific power features
(link-record columns, link-aware formulas, automation rules) stay in
``apps/boards/`` — see ``apps/boards/board_engine.py`` for those.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from emptyos.sdk.column_types import ColumnTypeRegistry as _CTR
from emptyos.sdk.vault_library import VaultLibrary

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


_TYPE_MAP = {tid: t.storage for tid, t in _CTR.all().items()}

PERSON_SINGLE_TYPES = tuple(
    tid for tid, t in _CTR.all().items() if t.person_like and not t.list_like
)
PERSON_MULTI_TYPES = tuple(
    tid for tid, t in _CTR.all().items() if t.person_like and t.list_like
)
ROLE_FOR_TYPE = {tid: t.role for tid, t in _CTR.all().items() if t.role}


class DynamicBoardLibrary(VaultLibrary):
    """A VaultLibrary configured at runtime from a board config dict.

    See module docstring for source types. ``filename`` arguments accept either
    a vault filename (vault_tag source) or an item id (app source).
    """

    def __init__(self, app: BaseApp, board_config: dict):
        self._board_config = board_config
        self._app_ref = app

        src = board_config.get("source")
        if not src:
            src = {"type": "vault_tag", "tag": board_config.get("source_tag", "")}
        self._source = src

        self.tag = src.get("tag", "") if src.get("type") == "vault_tag" else (
            board_config.get("source_tag", "")
        )

        columns = board_config.get("columns", [])
        self.fields = {}
        for col in columns:
            col_type = col.get("type", "text")
            self.fields[col["id"]] = _TYPE_MAP.get(col_type, str)

        # Set by get_items / set_field when the configured source app can't
        # be reached (uninstalled, failed to load, raised). Read by the boards
        # API to surface a banner instead of a 500.
        self._source_error: str | None = None

        super().__init__(app)

    # ── Source-aware item fetch ──────────────────────────────────────────

    async def get_items(self) -> list[dict]:
        """Fetch the full item set per the configured source. Async because
        ``app``-sourced boards call another app via ``call_app``."""
        stype = self._source.get("type", "vault_tag")
        if stype == "vault_tag":
            return self.list()

        if stype == "app":
            target = self._source.get("app")
            method = self._source.get("method", "list_all")
            if not target:
                return []
            try:
                result = await self._app_ref.call_app(target, method)
            except Exception as e:
                # Source app uninstalled, failed to load, or raised. Degrade to
                # empty list + record the error so the API can banner it.
                self._source_error = f"Source app '{target}' not available: {e}"
                return []
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("items", []) or []
            return []

        if stype == "mixed":
            out: list[dict] = []
            for sub in self._source.get("sources", []):
                sub_lib = DynamicBoardLibrary(
                    self._app_ref,
                    {**self._board_config, "source": sub},
                )
                sub_items = await sub_lib.get_items()
                stag = sub.get("type") + (":" + sub.get("app", sub.get("tag", ""))).rstrip(":")
                for it in sub_items:
                    it.setdefault("_source", stag)
                out.extend(sub_items)
            return out

        return []

    def list_filtered(self, filters: dict | None = None,
                      sort_by: str = "", sort_desc: bool = False,
                      group_by: str = "",
                      items: list[dict] | None = None) -> list[dict]:
        """Apply filter/sort/single-item formula eval over an item list.

        When ``items`` is provided, uses that list directly. Otherwise falls
        back to the synchronous vault read — only valid for
        ``source.type == 'vault_tag'``. Callers that may use an ``app``-sourced
        board MUST pre-fetch via ``await lib.get_items()`` and pass ``items=``.
        """
        if items is None:
            items = self.list()

        if filters:
            for key, val in filters.items():
                if val is None or val == "":
                    continue
                if isinstance(val, list):
                    items = [i for i in items if i.get(key) in val]
                else:
                    items = [i for i in items if str(i.get(key, "")).lower() == str(val).lower()]

        formula_cols = [c for c in self._board_config.get("columns", []) if c.get("type") == "formula"]
        if formula_cols:
            for item in items:
                for col in formula_cols:
                    expr = col.get("expression", "") or col.get("expr", "")
                    item[col["id"]] = _eval_formula(expr, item)

        if sort_by:
            items.sort(
                key=lambda i: (i.get(sort_by) or "") if isinstance(i.get(sort_by), str)
                else (i.get(sort_by) or 0),
                reverse=sort_desc,
            )

        return items

    # ── Source-aware CRUD ────────────────────────────────────────────────

    async def get_detail(self, filename: str) -> dict | None:
        stype = self._source.get("type", "vault_tag")
        if stype == "app":
            target = self._source.get("app")
            if not target:
                return None
            items = await self.get_items()
            for it in items:
                if (it.get("id") or it.get("file")) == filename:
                    return it
            return None
        return self.detail(filename)

    async def set_field(self, filename: str, field: str, value) -> dict:
        """Write one field on one item. Source-aware.

        - ``vault_tag``: regex-patch the note's frontmatter.
        - ``app``: ``call_app(source.app, "set_field", id=filename,
          field=..., value=...)`` — the source app is the system of record.
        """
        stype = self._source.get("type", "vault_tag")
        if stype == "app":
            target = self._source.get("app")
            if not target:
                return {"error": "source.app not set"}
            try:
                return await self._app_ref.call_app(
                    target, "set_field", id=filename, field=field, value=value,
                )
            except Exception as e:
                self._source_error = f"Source app '{target}' not available: {e}"
                return {"error": f"source app '{target}' not available"}
        return self.update(filename, {field: value})

    def aggregate(self, group_by: str = "", agg_field: str = "",
                  agg_fn: str = "count", items: list[dict] | None = None) -> dict:
        """Aggregate items for chart / dashboard views."""
        if items is None:
            items = self.list()
        if not group_by:
            return {"total": len(items)}

        groups: dict[str, list] = {}
        for item in items:
            key = str(item.get(group_by, "") or "unset")
            groups.setdefault(key, []).append(item)

        result: dict[str, Any] = {}
        for key, group_items in groups.items():
            if agg_fn == "count":
                result[key] = len(group_items)
            elif agg_fn == "sum" and agg_field:
                result[key] = sum(_to_float(i.get(agg_field, 0)) for i in group_items)
            elif agg_fn == "avg" and agg_field:
                vals = [_to_float(i.get(agg_field, 0)) for i in group_items]
                result[key] = sum(vals) / len(vals) if vals else 0

        return {"groups": result, "total": len(items)}


def _eval_formula(expression: str, item: dict) -> str:
    """Single-item formula eval. Link-aware multi-pass eval lives in
    ``apps/boards/board_engine.py`` ``evaluate_formulas`` because it depends
    on link-record column resolution, which is boards-specific."""
    if not expression:
        return ""
    from emptyos.sdk.formulas import evaluate, format_result
    normalized = re.sub(r"\{(\w+)\}", r"\1", expression)
    return format_result(evaluate(normalized, item, default="#ERR"))


def _to_float(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(val))
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0
