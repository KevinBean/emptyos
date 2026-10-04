"""Engineering evidence read model over app manifests and existing KB notes.

The source data stays where it already belongs:

* app/method and method/reference links live in app manifests;
* standard, clause, formula, ``implemented_in`` and ``verified_against``
  relationships live in KB note frontmatter.

This module only joins those sources for API, Boards, and Design Package use.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable

from emptyos.sdk import web_route

from .shared import _slug_of

if TYPE_CHECKING:
    from .app import KBApp  # noqa: F401


_WIKILINK_RE = re.compile(r"^\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]$")


def _values(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    out = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("slug") or item.get("reference") or item.get("standard")
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _target(value: str) -> tuple[str, bool]:
    """Return normalized target and whether the declaration was a wikilink."""
    match = _WIKILINK_RE.match(value.strip())
    if match:
        return match.group(1).strip(), True
    return value.strip(), False


def _note_slug(note: dict) -> str:
    return _slug_of(note.get("path", ""))


def _props(note: dict | None) -> dict:
    return (note or {}).get("properties", {}) or {}


def _relation_targets(note: dict) -> set[str]:
    props = _props(note)
    targets: set[str] = set()
    for key in ("related", "references", "verified_against"):
        for value in _values(props.get(key)):
            target, linked = _target(value)
            if linked or target:
                targets.add(target.lower())
    return targets


def _standard_keys(note: dict | None) -> set[str]:
    props = _props(note)
    standards = _values(props.get("standard_id")) + _values(props.get("standard"))
    editions = _values(props.get("edition"))
    keys = {value.lower() for value in standards if value}
    # An edition alone (for example "2016") must never join unrelated
    # standards. Composite keys preserve edition-specific matching without
    # turning every note from the same year into evidence for the method.
    keys.update(f"{standard.lower()}|{edition.lower()}"
                for standard in standards for edition in editions)
    return keys


def _manifest_raw(manifest) -> dict:
    return manifest.raw if hasattr(manifest, "raw") else (manifest or {})


def build_engineering_evidence(notes: Iterable[dict], manifests: dict) -> dict:
    """Join method references to the KB evidence ladder.

    The function is deliberately pure so coverage semantics can be tested
    without a running kernel or vault.
    """
    note_list = list(notes)
    by_slug = {_note_slug(note).lower(): note for note in note_list if _note_slug(note)}
    relations = {slug: _relation_targets(note) for slug, note in by_slug.items()}
    rows: list[dict] = []

    for app_id, manifest in sorted(manifests.items()):
        raw = _manifest_raw(manifest)
        methods = (raw.get("provides", {}) or {}).get("methods", {}) or {}
        if not isinstance(methods, dict):
            continue
        for analysis, entries in sorted(methods.items()):
            if isinstance(entries, dict):
                entries = [entries]
            for method in entries or []:
                if not isinstance(method, dict):
                    continue
                refs = _values(method.get("references"))
                # A method without references is itself an actionable gap.
                if not refs:
                    refs = [""]
                for declared in refs:
                    target, is_link = _target(declared)
                    anchor = by_slug.get(target.lower()) if target else None
                    anchor_slug = _note_slug(anchor) if anchor else target
                    anchor_props = _props(anchor)
                    std_keys = _standard_keys(anchor)

                    def connected(note: dict) -> bool:
                        slug = _note_slug(note).lower()
                        if anchor and slug == anchor_slug.lower():
                            return True
                        if anchor_slug and anchor_slug.lower() in relations.get(slug, set()):
                            return True
                        return bool(std_keys and std_keys.intersection(_standard_keys(note)))

                    clauses = [n for n in note_list
                               if _props(n).get("kind") == "clause" and connected(n)]
                    formulas = [n for n in note_list
                                if _props(n).get("kind") == "formula" and connected(n)]
                    if anchor and anchor_props.get("kind") == "formula" and anchor not in formulas:
                        formulas.append(anchor)

                    cases: dict[str, dict] = {}
                    unverified_formulas = []
                    for formula in formulas:
                        verified = _values(_props(formula).get("verified_against"))
                        if not verified:
                            unverified_formulas.append(_note_slug(formula))
                        for case_ref in verified:
                            case_slug, _ = _target(case_ref)
                            case = by_slug.get(case_slug.lower())
                            if case and _props(case).get("kind") == "case":
                                cases[_note_slug(case)] = case

                    if not target:
                        status = "unresolved"
                    elif is_link and not anchor:
                        status = "unresolved"
                    elif not formulas or unverified_formulas:
                        status = "unverified"
                    else:
                        status = "verified"

                    implemented = []
                    for formula in formulas:
                        refs_impl = _values(_props(formula).get("implemented_in"))
                        if refs_impl:
                            implemented.append({"formula": _note_slug(formula),
                                                "paths": refs_impl})

                    standard = (anchor_props.get("standard_id")
                                or anchor_props.get("standard")
                                or (anchor_props.get("title") if anchor_props.get("kind") == "reference" else "")
                                or (declared if declared and not is_link else ""))
                    rows.append({
                        "id": f"{app_id}:{analysis}:{method.get('id', 'default')}:{anchor_slug or 'missing'}",
                        "app_id": app_id,
                        "analysis": analysis,
                        "method_id": method.get("id") or "default",
                        "method": method.get("label") or method.get("id") or analysis,
                        "method_version": method.get("version") or "",
                        "reference": declared,
                        "reference_slug": anchor_slug or "",
                        "reference_kind": anchor_props.get("kind") or "",
                        "standard": standard,
                        "clauses": sorted(_note_slug(n) for n in clauses),
                        "formulas": sorted(_note_slug(n) for n in formulas),
                        "verification_cases": sorted(cases),
                        "implemented_formulas": implemented,
                        "unverified_formulas": sorted(unverified_formulas),
                        "status": status,
                    })

    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("verified", "unverified", "unresolved")}
    return {
        "summary": {
            "methods": len({(row["app_id"], row["analysis"], row["method_id"])
                            for row in rows}),
            "links": len(rows),
            **counts,
        },
        "rows": rows,
    }


async def engineering_evidence(self, *, app_ids=None, status: str = "") -> dict:
    manifests = dict(self.kernel.apps.manifests.items())
    result = build_engineering_evidence(self._all_notes(), manifests)
    wanted = {str(value) for value in (app_ids or []) if value}
    rows = result["rows"]
    if wanted:
        rows = [row for row in rows if row["app_id"] in wanted]
    if status:
        rows = [row for row in rows if row["status"] == status]
    counts = {value: sum(row["status"] == value for row in rows)
              for value in ("verified", "unverified", "unresolved")}
    return {
        "summary": {
            "methods": len({(row["app_id"], row["analysis"], row["method_id"])
                            for row in rows}),
            "links": len(rows),
            **counts,
        },
        "rows": rows,
    }


async def engineering_evidence_rows(self) -> list[dict]:
    """Flattened source for the generic Boards app."""
    result = await engineering_evidence(self)
    return [{
        **row,
        "clauses": ", ".join(row["clauses"]),
        "formulas": ", ".join(row["formulas"]),
        "verification_cases": ", ".join(row["verification_cases"]),
        "implemented_formulas": ", ".join(
            item["formula"] for item in row["implemented_formulas"]
        ),
    } for row in result["rows"]]


@web_route("GET", "/api/engineering-evidence")
async def api_engineering_evidence(self, request):
    app_ids = [value.strip() for value in
               (request.query_params.get("app") or "").split(",") if value.strip()]
    status = (request.query_params.get("status") or "").strip().lower()
    if status not in {"", "verified", "unverified", "unresolved"}:
        return {"error": "status must be verified, unverified, or unresolved"}
    return await engineering_evidence(self, app_ids=app_ids, status=status)
