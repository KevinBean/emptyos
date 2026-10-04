"""Engineering-discipline roster discovery — the ``[[contributes.design-package.section]]`` set.

"Which engineering apps expose a linkable entity, and what entities each offers"
is read by two apps: ``design-package`` (the deliverable assembler) and
``substation-project`` (the design-basis spine). Both read the SAME manifest
contribution slot, so this is the one discovery, extracted at the second consumer
(CLAUDE.md rule 9).

``section_contributors`` reads *raw* manifests (not ``get_contributions``, which
filters to loaded apps) because the picker greys out unavailable participants
rather than hiding them. ``candidates_for`` is the per-contributor entity list,
fetched via the ``link_source`` method named in the manifest entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


def section_contributors(manifests, *, exclude_app_id: str | None = None) -> list[dict]:
    """Every ``[[contributes.design-package.section]]`` entry across all
    *discovered* manifests (loaded or not), ordered by the entry's ``order``.

    Returns rows ``{app_id, label, order, link_source, view_url}``.
    ``exclude_app_id`` drops the caller's own app — a ``substation-project``
    declares a section (so it can roll into a package) yet must not offer to link
    to itself in its own roster.
    """
    rows: list[dict] = []
    for app_id, manifest in manifests.items():
        if exclude_app_id is not None and app_id == exclude_app_id:
            continue
        entries = (manifest.raw.get("contributes", {})
                   .get("design-package", {}).get("section"))
        if not entries:
            continue
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            if not isinstance(e, dict):
                continue
            rows.append({
                "app_id": app_id,
                "label": e.get("title") or app_id,
                "order": e.get("order", 100),
                "link_source": e.get("link_source"),
                "view_url": e.get("view_url"),
            })
    rows.sort(key=lambda r: (r["order"], r["app_id"]))
    return rows


def apply_basis_map(basis: dict | None, mapping: dict) -> tuple[dict, dict]:
    """Apply a design-basis → discipline-field mapping for a ``seed_from_basis``.

    ``mapping`` is ``{basis_key: target_field}``. Basis keys that are absent or
    ``None`` are skipped — a seed only writes what the basis actually declares.
    Returns ``(updates, patch)``: ``updates`` keyed by the discipline's own
    field names (splice into its model/spec/scenario), ``patch`` keyed by the
    basis names (returned to the caller for display, and what substation-project
    snapshots for stale-seed detection). Shared by the three seed implementations
    (earthing / overhead-line / sc-force); each keeps its own storage read,
    validation gate, write, and emit — that's where they genuinely differ.
    """
    basis = basis or {}
    updates: dict = {}
    patch: dict = {}
    for basis_key, target_field in mapping.items():
        v = basis.get(basis_key)
        if v is not None:
            updates[target_field] = v
            patch[basis_key] = v
    return updates, patch


async def candidates_for(app: "BaseApp", row: dict) -> list[dict]:
    """``(id, label)`` entities a contributor exposes for linking, via the
    ``link_source`` method named in its manifest entry. Best-effort — an app
    without ``link_source`` (or whose call fails / is unloaded) lists nothing."""
    method = row.get("link_source")
    if not method:
        return []
    try:
        items = await app.call_app(row["app_id"], method)
    except Exception:
        return []
    out = []
    for r in items or []:
        if isinstance(r, dict) and r.get("id"):
            out.append({
                "id": r["id"],
                "label": (r.get("label") or r.get("title")
                          or r.get("name") or r["id"]),
            })
    return out
