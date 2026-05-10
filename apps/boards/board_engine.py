"""Boards-specific glue around ``emptyos.sdk.board_engine``.

The generic source / filter / sort / aggregate engine lives in
``emptyos/sdk/board_engine.py`` so any app can use it. This module keeps the
boards-only pieces:

  - ``BoardConfigStore`` — JSON board configs persisted to the vault.
  - ``evaluate_formulas`` — link-aware multi-pass formula eval (depends on
    link-record columns + boards' preset registry).

The column-type constants and ``DynamicBoardLibrary`` are re-exported for
back-compat so existing import sites (``app.py``, ``export.py``) don't need
to change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

# Re-exports — back-compat for existing callers.
from emptyos.sdk.board_engine import (  # noqa: F401
    PERSON_MULTI_TYPES,
    PERSON_SINGLE_TYPES,
    ROLE_FOR_TYPE,
    DynamicBoardLibrary,
    _eval_formula,
    _to_float,
)

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


class BoardConfigStore:
    """Reads and writes board configuration files from the vault.

    Board configs are stored as JSON files (not YAML frontmatter) because
    they contain deeply nested structures (columns, views, rules) that the
    platform's ``parse_frontmatter`` cannot handle.
    """

    def __init__(self, app: BaseApp):
        self.app = app

    def _boards_dir(self) -> Path:
        return self.app.vault_config_path("boards_dir", "30_Resources/EmptyOS/boards") or Path(".")

    def list_boards(self) -> list[dict]:
        d = self._boards_dir()
        if not d.exists():
            return []
        boards = []
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("_"):
                continue
            try:
                import json

                config = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(config, dict):
                    continue
                boards.append(
                    {
                        "id": config.get("id", f.stem),
                        "name": config.get("name", f.stem),
                        "description": config.get("description", ""),
                        "source_tag": config.get("source_tag", ""),
                        "type": config.get("type", "board"),
                        "column_count": len(config.get("columns", [])),
                        "view_count": len(config.get("views", [])),
                    }
                )
            except Exception:
                continue
        return boards

    def get_board(self, board_id: str) -> dict | None:
        import json

        d = self._boards_dir()
        target = d / f"{board_id}.json"
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                return None

        if d.exists():
            for f in d.glob("*.json"):
                try:
                    config = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(config, dict) and config.get("id") == board_id:
                        return config
                except Exception:
                    continue
        return None

    def save_board(self, board_id: str, config: dict) -> Path:
        import json

        d = self._boards_dir()
        d.mkdir(parents=True, exist_ok=True)

        clean = {k: v for k, v in config.items() if not k.startswith("_")}
        clean.setdefault("id", board_id)

        target = d / f"{board_id}.json"
        target.write_text(
            json.dumps(clean, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def delete_board(self, board_id: str) -> bool:
        d = self._boards_dir()
        target = d / f"{board_id}.json"
        if target.exists():
            target.unlink()
            return True
        return False


async def evaluate_formulas(app: BaseApp, config: dict, items: list[dict]) -> list[dict]:
    """Link-aware multi-pass formula eval. For every formula column on the
    board, evaluate against each item with link-record columns pre-resolved
    to lists of target item dicts. Mutates ``items`` in place and returns
    them.

    Stays in boards because link-record columns are a boards-only feature.
    """
    from emptyos.sdk.formulas import evaluate, format_result

    columns = config.get("columns", [])
    formula_cols = [c for c in columns if c.get("type") == "formula"]
    if not formula_cols:
        return items
    link_cols = [c for c in columns if c.get("type") == "link-record"]

    target_boards = {c.get("target_board") for c in link_cols if c.get("target_board")}
    board_caches: dict[str, dict[str, dict]] = {}
    for tb in target_boards:
        tb_cfg = None
        store = getattr(app, "_store", None)
        if store:
            tb_cfg = store.get_board(tb)
        if tb_cfg is None:
            from .presets import get_preset

            tb_cfg = get_preset(tb)
        if not tb_cfg:
            board_caches[tb] = {}
            continue
        tb_lib = DynamicBoardLibrary(app, tb_cfg)
        try:
            tb_items = await tb_lib.get_items()
        except Exception:
            tb_items = []
        board_caches[tb] = {
            (it.get("file") or it.get("id") or ""): it
            for it in tb_items
            if (it.get("file") or it.get("id"))
        }

    for item in items:
        ctx = dict(item)
        for col in link_cols:
            tb = col.get("target_board")
            if not tb:
                continue
            ids_val = item.get(col["id"])
            if ids_val is None or ids_val == "":
                ids = []
            elif isinstance(ids_val, list):
                ids = [str(x) for x in ids_val if x]
            elif isinstance(ids_val, str):
                ids = [s.strip() for s in ids_val.split(",") if s.strip()]
            else:
                ids = [str(ids_val)]
            cache = board_caches.get(tb) or {}
            ctx[col["id"]] = [cache[i] for i in ids if i in cache]

        for col in formula_cols:
            expr = col.get("expression", "") or col.get("expr", "")
            if not expr:
                continue
            normalized = re.sub(r"\{(\w+)\}", r"\1", expr)
            result = evaluate(normalized, ctx, default="#ERR")
            item[col["id"]] = format_result(result)
    return items
