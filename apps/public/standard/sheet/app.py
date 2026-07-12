"""Sheet — a light calc sheet (the one genuine Excel-shaped gap).

A grid of cells stored as a markdown table in a vault note. Cells hold
**formula source** (`=SUM(B2:B10)`, `=IF(A1>10,"hi","lo")`, `=TODAY()`) or
literals — the source is the truth. Computed values are derived on read by
the pure ``engines.sheet`` engine and never persisted (they are dependent
appearances, not ground truth — three-natures lens). The note stays
hand-editable in any markdown editor.

Deterministic, no LLM: this app declares no `think` capability. Boards is a
records-with-fields *view* layer with read-only column formulas; this is the
free-form editable-cell grid boards is not.
"""

from __future__ import annotations

from datetime import date

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.formulas import format_result
from emptyos.sdk.utils import safe_note_filename
from emptyos.sdk.vault_library import VaultLibrary

from engines.sheet import Grid, parse_table, recompute, serialize_table

MAX_DIM = 50  # light sheet — cap rows/cols so the markdown table stays sane


class SheetLibrary(VaultLibrary):
    tag = "sheet"
    fields = {
        "title": str,
        "created": str,
        "updated": str,
        "rows": int,
        "cols": int,
        "author": str,
    }
    sort_key = "updated"
    sort_reverse = True
    search_fields = ["title"]
    fallback_folder = "30_Resources/EmptyOS/sheet/sheets"


def _computed_display(grid: Grid) -> dict[str, str]:
    """Recompute then stringify each cell for display (raw stays the source)."""
    return {ref: format_result(val) for ref, val in recompute(grid).items()}


class SheetApp(BaseApp):
    async def setup(self):
        await super().setup()
        self.sheets = SheetLibrary(self)

    # ── helpers ───────────────────────────────────────────────────────

    def _sheet_rel(self, filename: str) -> str:
        return self.vault_rel(self.vault_path(f"sheets/{filename}")) or (
            f"30_Resources/EmptyOS/sheet/sheets/{filename}"
        )

    def _reindex(self, filename: str):
        rel = self._sheet_rel(filename)
        if rel:
            self.vault_force_index(rel)

    def _load_grid(self, filename: str):
        """Return (item, grid) or (None, None) if the sheet is missing."""
        item = self.sheets.detail(filename)
        if not item:
            return None, None
        grid = parse_table(item.get("body", ""))
        # Trust frontmatter dims when present (an empty table parses to 1×1).
        grid.rows = max(grid.rows, int(item.get("rows") or 1))
        grid.cols = max(grid.cols, int(item.get("cols") or 1))
        return item, grid

    def _payload(self, item: dict, grid: Grid) -> dict:
        return {
            "id": item["file"],
            "title": item.get("title") or item["file"],
            "rows": grid.rows,
            "cols": grid.cols,
            "raw_grid": grid.raw_grid(),
            "computed_grid": _computed_display(grid),
            "_vault_path": self._sheet_rel(item["file"]),
        }

    def _save_body(self, filename: str, grid: Grid):
        """Re-serialize the table body and bump `updated`. Caller holds the lock."""
        self.sheets.write_body(filename, serialize_table(grid))
        self.sheets.update(
            filename,
            {"updated": date.today().isoformat(), "rows": grid.rows, "cols": grid.cols},
        )
        self._reindex(filename)

    # ── routes ────────────────────────────────────────────────────────

    @web_route("GET", "/api/sheets")
    async def api_list(self, request):
        rows = [
            {
                "id": s["file"],
                "title": s.get("title") or s["file"],
                "rows": s.get("rows") or 0,
                "cols": s.get("cols") or 0,
                "updated": s.get("updated") or "",
            }
            for s in self.sheets.list()
        ]
        return {"sheets": rows}

    @web_route("POST", "/api/sheets")
    async def api_create(self, request):
        data = await self.safe_json(request)
        title = (data.get("title") or "Untitled sheet").strip()
        rows = max(1, min(MAX_DIM, int(data.get("rows") or self.app_config("default_rows", 12))))
        cols = max(1, min(MAX_DIM, int(data.get("cols") or self.app_config("default_cols", 8))))
        filename = safe_note_filename(
            self.vault_dir / "sheets", title, fallback_prefix="sheet"
        )
        today = date.today().isoformat()
        grid = Grid(rows=rows, cols=cols)
        fm = {
            "title": title,
            "created": today,
            "updated": today,
            "rows": rows,
            "cols": cols,
            "author": "user",
            "tags": ["sheet"],
        }
        self.vault_create_note(self._sheet_rel(filename), fm, serialize_table(grid))
        await self.emit("sheet:created", {"id": filename, "title": title})
        return {"ok": True, "id": filename}

    @web_route("GET", "/api/sheets/{id}")
    async def api_get(self, request):
        item, grid = self._load_grid(request.path_params.get("id", ""))
        if not item:
            return {"error": "sheet not found"}
        return self._payload(item, grid)

    @web_route("POST", "/api/sheets/{id}/cell")
    async def api_set_cell(self, request):
        filename = request.path_params.get("id", "")
        if not filename.endswith(".md"):
            filename += ".md"
        data = await self.safe_json(request)
        cell = (data.get("cell") or "").strip().upper()
        raw = data.get("raw", "")
        if not cell:
            return {"error": "cell is required"}

        # Serialize the read-modify-write so a racing writer can't wipe it.
        async with self.write_lock(f"sheet:{filename}"):
            item, grid = self._load_grid(filename)
            if not item:
                return {"error": "sheet not found"}
            try:
                col, row = _parse_cell(cell)
            except ValueError:
                return {"error": f"bad cell reference: {cell}"}
            if col >= grid.cols or row > grid.rows:
                return {"error": f"cell {cell} is outside the sheet ({grid.rows}×{grid.cols})"}
            grid.set(cell, str(raw))
            self._save_body(filename, grid)

        await self.emit("sheet:cell_changed", {"id": filename, "cell": cell})
        return {
            "ok": True,
            "cell": cell,
            "computed_grid": _computed_display(grid),
        }

    @web_route("POST", "/api/sheets/{id}/resize")
    async def api_resize(self, request):
        filename = request.path_params.get("id", "")
        if not filename.endswith(".md"):
            filename += ".md"
        data = await self.safe_json(request)
        async with self.write_lock(f"sheet:{filename}"):
            item, grid = self._load_grid(filename)
            if not item:
                return {"error": "sheet not found"}
            if data.get("rows") is not None:
                grid.rows = max(1, min(MAX_DIM, int(data["rows"])))
            if data.get("cols") is not None:
                grid.cols = max(1, min(MAX_DIM, int(data["cols"])))
            # Drop cells that fell outside the new bounds.
            for ref in list(grid.cells):
                c, r = _parse_cell(ref)
                if c >= grid.cols or r > grid.rows:
                    grid.cells.pop(ref, None)
            self._save_body(filename, grid)
        return {"ok": True, "rows": grid.rows, "cols": grid.cols}

    @web_route("DELETE", "/api/sheets/{id}")
    async def api_delete(self, request):
        filename = request.path_params.get("id", "")
        if not filename.endswith(".md"):
            filename += ".md"
        path = self.sheets.find_file(filename)
        if not path:
            return {"error": "sheet not found"}
        path.unlink()
        self._reindex(filename)
        await self.emit("sheet:deleted", {"id": filename})
        return {"ok": True}

    @web_route("GET", "/api/sheets/{id}/export.csv")
    async def api_export_csv(self, request):
        import csv
        import io

        from starlette.responses import Response

        from engines.sheet.grid import to_a1

        item, grid = self._load_grid(request.path_params.get("id", ""))
        if not item:
            return {"error": "sheet not found"}
        computed = _computed_display(grid)
        buf = io.StringIO()
        w = csv.writer(buf)
        for r in range(1, grid.rows + 1):
            w.writerow([computed.get(to_a1(c, r), "") for c in range(grid.cols)])
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{item["file"]}.csv"'},
        )


def _parse_cell(ref: str) -> tuple[int, int]:
    from engines.sheet.grid import parse_a1

    return parse_a1(ref)
