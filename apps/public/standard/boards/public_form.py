"""boards — the public Form view: schema-generic, unauthenticated submission.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the two `public_routes`-exempt endpoints that let an anonymous
visitor read a board's fillable schema and submit one record — the Airtable
"Form view" analogue (`.claude/rules/boards-as-view-layer.md`). Distinct from
BookMe's booking form (hardcoded fields for one domain); this is generic over
ANY board's column schema, same shape any other view renders.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._store / self._create_item_from_fields
(spine, from items.py) for board resolution + the shared create path.
Do not import from ``.app`` (it imports us, which would cycle).

Security posture (`.claude/rules/public-app-pattern.md`): these routes are
dedicated-public (never shared with an authenticated CRUD path), so unlike a
route serving both faces there's no `is_public_request()` branch — every
caller, authenticated or not, gets the same filtered read and the same
validated write. `rollup`/`formula`/`link-record` columns never appear in the
schema this returns and any such key in a submission is silently dropped
before validation — a stranger can't see or set a computed/relational field.
Every remaining field is coerced + validated the same way `CollectionLibrary`
now does for its own writes (`emptyos/sdk/column_types.py`), so a required
field left empty is refused with a real error, not silently defaulted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .presets import get_preset

from emptyos.sdk import web_route
from emptyos.sdk.column_types import ColumnTypeRegistry, columns_to_form_fields
from emptyos.sdk.utils import path_segment_error

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# Never offered on a public form: a formula/rollup is computed (never
# fill-in), a link-record needs the same async picker the authenticated
# add-form uses (deferred — a stranger picking from another board's private
# records is also a separate exposure question worth its own review).
_PUBLIC_FORM_EXCLUDED_TYPES = {"formula", "rollup", "link-record"}


# ─── Bind to BoardsApp class as ────────────────────────────────
#   public_form_page        = _public_form.public_form_page
#   api_public_form_schema  = _public_form.api_public_form_schema
#   api_public_form_submit  = _public_form.api_public_form_submit
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/f/{id}")
async def public_form_page(self, request):
    """Serve the standalone public form page (pages/form.html). Not the
    authenticated `/boards/` SPA — a stranger with no login must never see
    that shell. The board id in the URL is read client-side (same shape as
    BookMe's `/b/<event>`); this handler just returns the static page."""
    from starlette.responses import HTMLResponse

    page = Path(__file__).parent / "pages" / "form.html"
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


def _fillable_columns(config: dict) -> list[dict]:
    return [
        c for c in (config.get("columns") or [])
        if c.get("type") not in _PUBLIC_FORM_EXCLUDED_TYPES
    ]


@web_route("GET", "/api/public/schema/{id}")
async def api_public_form_schema(self, request):
    board_id = request.path_params.get("id", "")
    err = path_segment_error(board_id, "board id")
    if err:
        return {"error": err}
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "board not found"}
    return {
        "id": board_id,
        "name": config.get("name", board_id),
        "description": config.get("description", ""),
        "fields": columns_to_form_fields(_fillable_columns(config)),
    }


@web_route("POST", "/api/public/submit/{id}")
async def api_public_form_submit(self, request):
    board_id = request.path_params.get("id", "")
    err = path_segment_error(board_id, "board id")
    if err:
        return {"error": err}
    config = self._store.get_board(board_id) or get_preset(board_id)
    if not config:
        return {"error": "board not found"}

    raw = await request.json()
    if not isinstance(raw, dict):
        return {"error": "invalid submission"}

    fillable = _fillable_columns(config)
    fillable_ids = {c["id"] for c in fillable}
    # Drop anything not a real fillable column on THIS board — a stranger
    # cannot smuggle a rollup/link-record value, nor any key the schema
    # doesn't declare (tags/created/body/etc.).
    data = {k: v for k, v in raw.items() if k in fillable_ids}

    errors: list[str] = []
    clean: dict = {}
    for col in fillable:
        col_id = col["id"]
        ctype = ColumnTypeRegistry.get(col.get("type") or "text")
        if col_id not in data:
            if col.get("required") and col.get("default") is None:
                try:
                    ctype.validate("", col)
                except ValueError as e:
                    errors.append(str(e))
            continue
        value = ctype.coerce(data[col_id], col)
        try:
            ctype.validate(value, col)
        except ValueError as e:
            errors.append(str(e))
            continue
        clean[col_id] = value
    if errors:
        return {"error": "; ".join(errors)}

    return await self._create_item_from_fields(board_id, config, clean)
