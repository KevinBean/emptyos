"""VaultLibrary subclasses for the Orgs app.

Two collections, both rooted at `ORG_ROOT`:

  - OrgLibrary    → notes tagged `org`        in ORG_ROOT/<org_id>/<org_id>.md
  - MemberLibrary → notes tagged `org-member` in ORG_ROOT/<org_id>/members/<mid>.md

One Org primitive carries identity (vision / mission / values / culture / roles)
plus orthogonal axes:
  - `reality`: real (the org exists in the world) | virtual (a sim you run)
  - `scope`:   member (you're in it) | external (you track it but aren't in it)
  - `kind`:    team / household / side-business / community / employer / vendor / other

One Member primitive carries the org↔participant edge, discriminated by `mode`:
  - mode=human → `person_id` references a note in apps/people/
  - mode=ai    → carries `name`, `system_prompt` (body section), `model`, `emoji`

Both libraries fold in what used to be three separate collections (Company,
Worker, Membership). The legacy company-app data at the old `30_Resources/
EmptyOS/company/` path is migrated forward at boot — see `app.py:_migrate_*`.
"""

from __future__ import annotations

from emptyos.sdk.vault_library import VaultLibrary

ORG_ROOT = "30_Resources/EmptyOS/org"


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _scope_to_org_root(items: list[dict]) -> list[dict]:
    return [
        i for i in items
        if (i.get("path") or "").replace("\\", "/").startswith(ORG_ROOT + "/")
    ]


class OrgLibrary(VaultLibrary):
    tag = "org"
    fields = {
        "id": str,
        "name": str,
        "kind": str,
        "reality": str,
        "scope": str,
        "vision": str,
        "mission": str,
        "values": str,   # comma-separated; rendered as chips
        "culture": str,
        "roles": str,    # comma-separated; org-chart positions
        "parent": str,
        "company_id": str,
        "archived": str,
        "created": str,
    }
    sort_key = "name"
    fallback_folder = ORG_ROOT
    fallback_glob = "**/*.md"

    def list(self, **filters):
        include_archived = filters.pop("include_archived", False)
        scoped = _scope_to_org_root(super().list(**filters))
        # Org notes live at ORG_ROOT/<id>/<id>.md — guard out member notes.
        scoped = [
            i for i in scoped
            if "/members/" not in (i.get("path") or "").replace("\\", "/")
        ]
        if not include_archived:
            scoped = [i for i in scoped if not _truthy(i.get("archived"))]
        return scoped


class MemberLibrary(VaultLibrary):
    tag = "org-member"
    fields = {
        "id": str,
        "org_id": str,
        "mode": str,           # "human" | "ai"
        "role": str,
        "dept": str,
        "reports_to": str,
        # human-only
        "person_id": str,
        "joined": str,
        "left": str,
        # ai-only
        "name": str,
        "model": str,
        "emoji": str,
        # state
        "archived": str,
        "created": str,
    }
    sort_key = "created"
    fallback_folder = ORG_ROOT
    fallback_glob = "**/members/*.md"

    def list(self, **filters):
        include_archived = filters.pop("include_archived", False)
        scoped = _scope_to_org_root(super().list(**filters))
        scoped = [
            i for i in scoped
            if "/members/" in (i.get("path") or "").replace("\\", "/")
        ]
        if not include_archived:
            scoped = [i for i in scoped if not _truthy(i.get("archived"))]
        return scoped
