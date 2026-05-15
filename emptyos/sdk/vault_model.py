"""VaultModel — Pydantic-backed typed view over vault frontmatter.

Vault frontmatter is YAML, hand-edited, and decades-tolerant. Apps that
read it today do their own ad-hoc coercion at every call site:

    score = props.get("match_score", "")
    try:
        score = int(score) if score else None
    except (ValueError, TypeError):
        score = None
    status = props.get("status") or ""
    if not status:
        if props.get("Application_Sent") in ("true", "True"): status = "interview"
        elif props.get("Application") in ("true", "True"): status = "applied"
        elif props.get("Active") in ("false", "False"): status = "rejected"

This module collapses that pattern into one declaration per note type:

    class JobApplication(VaultModel):
        TAG = "job-application"

        company: str
        role: str = ""
        status: Literal["applied","interview","offer","rejected","withdrawn"] = "applied"
        match_score: int | None = None
        salary: str = ""
        priority: int | None = None
        created: date | None = None

        # Legacy frontmatter fields → modern shape.
        _legacy_status = legacy_alias(
            "Application_Sent", lambda v: "interview" if str(v).lower() == "true" else None,
            "Application",     lambda v: "applied"   if str(v).lower() == "true" else None,
            "Active",          lambda v: "rejected"  if str(v).lower() == "false" else None,
            into="status",
        )

Then in the app:

    apps = JobApplication.read_all(self)                  # tag-scoped query + parse
    one = JobApplication.read(self, path)                 # single note
    JobApplication.update(self, path, status="offer")     # validates before write
    {"company": "...", ...} = one.model_dump(mode="json") # JSON-safe for API responses

Design rules:
- `extra = "allow"` so unknown frontmatter survives a round-trip. Apps add
  fields without forcing schema bumps on every legacy note.
- `mode="before"` validators handle the YAML-string-everything reality
  (numbers come back as strings, bools as "true"/"false"/"True"/"False").
- `SETTABLE_FIELDS` is derived from the model — no hand-maintained whitelist
  drift. Boards' `set_field` contract reads it directly.
- The helper does NOT replace VaultIndex. It wraps `vault_query`,
  `vault_get_properties`, `vault_update`, `vault_create_note`.
- Validation failures on read return None + log; they never raise. Apps
  shouldn't crash because one legacy note has bad data.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator

log = logging.getLogger(__name__)


# ── Coercion helpers (mode="before" validators) ─────────────────────────


def coerce_int_or_none(v: Any) -> int | None:
    """YAML often returns numeric frontmatter as strings."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


def coerce_float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"true", "yes", "1", "on"}


def coerce_date_or_none(v: Any) -> date | None:
    """Accept ISO date strings, datetimes, dates."""
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    return None


# ── VaultModel base ─────────────────────────────────────────────────────


class VaultModel(BaseModel):
    """Typed view over a vault note's frontmatter.

    Subclasses set `TAG` (and optionally `FOLDER`) and declare typed fields.
    Subclasses can override `_legacy_aliases()` to merge old field names into
    the modern shape during validation.
    """

    model_config = ConfigDict(
        extra="allow",           # unknown frontmatter passes through
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # Required class-level metadata (subclasses set these).
    TAG: ClassVar[str] = ""
    FOLDER: ClassVar[str] = ""   # optional default folder for vault_query

    # Populated by the read pipeline; not part of the persisted frontmatter.
    _vault_path: str = PrivateAttr(default="")
    _vault_name: str = PrivateAttr(default="")

    # ── Hooks subclasses override ──

    @classmethod
    def _legacy_aliases(cls, raw: dict) -> dict:
        """Translate legacy frontmatter keys into modern field names.

        Default: no-op. Subclasses override to map e.g.
        `Application_Sent: true` → `status: "interview"`.
        Mutates and returns `raw`.
        """
        return raw

    # ── Read API ──

    @classmethod
    def from_query_row(cls, row: dict) -> "VaultModel | None":
        """Parse a row from `BaseApp.vault_query` into a model instance.

        Returns None (and logs) on validation failure so one bad note
        doesn't blow up the whole list.
        """
        props = dict(row.get("properties", {}) or {})
        props = cls._legacy_aliases(props)
        try:
            inst = cls.model_validate(props)
        except Exception as e:
            log.warning(
                "VaultModel %s rejected %s: %s",
                cls.__name__, row.get("path", "?"), e,
            )
            return None
        inst._vault_path = row.get("path", "")
        inst._vault_name = row.get("name", "")
        return inst

    _FOLDER_UNSET: ClassVar[str] = "__unset__"

    @classmethod
    def read_all(cls, app, *, folder: str | None = _FOLDER_UNSET) -> list["VaultModel"]:
        """Tag-scoped read of all notes of this model's type.

        `app` is a BaseApp instance — uses its `vault_query`.
        Pass `folder=None` to explicitly bypass the class default FOLDER.
        """
        if not cls.TAG:
            raise ValueError(f"{cls.__name__} must set TAG")
        if folder == cls._FOLDER_UNSET:
            folder = cls.FOLDER or None
        rows = app.vault_query(tags=[cls.TAG], folder=folder)
        out: list[VaultModel] = []
        for r in rows:
            inst = cls.from_query_row(r)
            if inst is not None:
                out.append(inst)
        return out

    @classmethod
    def read(cls, app, rel_path: str) -> "VaultModel | None":
        """Read a single note by relative path."""
        props = app.vault_get_properties(rel_path)
        if not props:
            return None
        return cls.from_query_row({"path": rel_path, "properties": props,
                                   "name": rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]})

    # ── Write API ──

    @classmethod
    def settable_fields(cls) -> set[str]:
        """Field whitelist for boards/external `set_field` calls.

        Defaults to every declared (non-private) field. Subclasses can
        override to narrow — e.g. exclude `created` (immutable) or `notes`
        (free-form text the user edits in the note body).
        """
        return {name for name in cls.model_fields if not name.startswith("_")}

    @classmethod
    def update(cls, app, rel_path: str, **fields) -> dict:
        """Validate `fields` against the schema, then write to the vault.

        Returns:
            {"ok": True}                          on success
            {"error": "field 'X' not settable"}   if a field is off-whitelist
            {"error": "validation: ..."}          if a value fails validation

        Reads the current note first so partial updates validate against the
        merged record (e.g. enum transitions are sane).
        """
        settable = cls.settable_fields()
        for k in fields:
            if k not in settable:
                return {"error": f"field '{k}' not settable"}

        current = cls.read(app, rel_path)
        if current is None:
            # No current record — validate the partial as a creation.
            try:
                cls.model_validate(fields)
            except Exception as e:
                return {"error": f"validation: {e}"}
        else:
            try:
                for k, v in fields.items():
                    setattr(current, k, v)  # ConfigDict.validate_assignment=True
            except Exception as e:
                return {"error": f"validation: {e}"}

        app.vault_update(rel_path, fields)
        return {"ok": True}

    def to_frontmatter(self) -> dict:
        """Serialize for vault_create_note. Dates → ISO strings, drops privates."""
        out: dict[str, Any] = {}
        for k, v in self.model_dump(mode="json", exclude_none=False).items():
            if k.startswith("_"):
                continue
            out[k] = v
        return out


# ── Common validator helpers exposed for subclasses ────────────────────


def int_or_none_validator(field: str):
    """Returns a validator that coerces string/empty to int | None."""
    return field_validator(field, mode="before")(lambda cls, v: coerce_int_or_none(v))


def float_or_none_validator(field: str):
    return field_validator(field, mode="before")(lambda cls, v: coerce_float_or_none(v))


def bool_validator(field: str):
    return field_validator(field, mode="before")(lambda cls, v: coerce_bool(v))


def date_or_none_validator(field: str):
    return field_validator(field, mode="before")(lambda cls, v: coerce_date_or_none(v))
