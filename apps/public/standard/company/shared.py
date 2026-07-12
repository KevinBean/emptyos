"""company — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (assets/boards/dispatch/members/migration/orgs/routes) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

from emptyos.sdk import now_iso as _now


def _vault_rel_org(org_id: str) -> str:
    return f"30_Resources/EmptyOS/org/{org_id}/{org_id}.md"


def _vault_rel_member(org_id: str, member_id: str) -> str:
    return f"30_Resources/EmptyOS/org/{org_id}/members/{member_id}.md"


# Legacy paths — only used by the boot-time migration. Once `.migrated_v0.3_org_unification`
# sentinel is written, nothing else touches these. Old notes stay on disk as orphans
# until the user manually cleans `30_Resources/EmptyOS/company/`.
def _legacy_rel_company(cid: str) -> str:
    return f"30_Resources/EmptyOS/company/{cid}/{cid}.md"


def _legacy_rel_worker(cid: str, wid: str) -> str:
    return f"30_Resources/EmptyOS/company/{cid}/workers/{wid}.md"


# Legacy folder prefix used by the v0.3 migration startswith filter.
_LEGACY_COMPANY_PREFIX = "30_Resources/EmptyOS/company/"
