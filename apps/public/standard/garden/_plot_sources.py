"""garden — single source of truth for reads from other apps.

V1 ships 4 plots (physical / social / intellectual / occupational). Each
reader returns a `PlotData` dict with `slug`, `species`, and a list of
`Plant` dicts. All reads are defensive — any failure degrades to an empty
plot. Constraint 1 of the plan: zero coupling out. We read; we never
crash.

Plant dataclass shape (kept loose as a dict for JSON-cache friendliness):

    {
        "id":          str,          # entity id (for click → timeline4D)
        "species":     str,          # "wildflower" | "bamboo" | "sapling" | "grass"
        "stage":       str,          # seedling | budding | evergreen | wilting | dormant
        "health":      float,        # 0..1, currently unused but reserved
        "entity_path": str,          # vault rel path for timeline4D click-through
        "seed":        int,          # stable per-id (grammar RNG)
        "label":       str,          # short display label
        "days_since":  int,          # days since last_touched (for appleton annotations)
    }
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

log = logging.getLogger("emptyos.garden.sources")


# ── Stage classifier ──────────────────────────────────────────────
def _days_since(value: Any) -> int:
    """Days from `value` to today. Returns 999 if unparseable.

    Accepts ISO strings, date/datetime, and unix timestamps (float/int) —
    the VaultIndex stores file mtime as a float under `modified`.
    """
    if not value:
        return 999
    try:
        if isinstance(value, datetime):
            d = value.date()
        elif isinstance(value, date):
            d = value
        elif isinstance(value, (int, float)):
            d = datetime.fromtimestamp(value).date()
        elif isinstance(value, str):
            d = datetime.fromisoformat(value[:10]).date()
        else:
            return 999
        return max(0, (date.today() - d).days)
    except Exception:
        return 999


def classify_stage(last_touched: Any, created: Any = None,
                   path: str = "") -> tuple[str, int]:
    """Return (stage, days_since_touched).

    Rules (per plan):
        archive folder    → dormant
        created < 3d ago  → seedling
        touched < 7d      → budding
        7-30d             → evergreen
        30-60d            → wilting
        60d+              → dormant (never dies)
    """
    if path and "40_Archive/" in path:
        return ("dormant", _days_since(last_touched))

    days_created = _days_since(created) if created else 999
    if days_created < 3:
        return ("seedling", days_created)

    days = _days_since(last_touched)
    if days < 7:
        return ("budding", days)
    if days < 30:
        return ("evergreen", days)
    if days < 60:
        return ("wilting", days)
    return ("dormant", days)


def _stable_seed(s: str) -> int:
    return abs(hash(s)) % (2**31)


# ── Plot readers ──────────────────────────────────────────────────

async def read_physical(app, window_days: int = 30) -> dict[str, Any]:
    """Grass plot — short blades, one per recent physical-tagged journal day
    or capture. Intensity → blade count via grammar's vigour scaling.

    No specific entities to land on; each blade is a faceless signal. Plants
    have empty entity_path → grammar will skip click affordance.
    """
    plants: list[dict] = []
    try:
        from emptyos.runtime import wheel
        signals = wheel.collect_signals(app.kernel, window_days)
        raw = int(signals.get("physical", 0) or 0)
        # One grass clump per ~2 signal units, min 1 if any signal at all.
        n = min(8, max(1 if raw > 0 else 0, (raw + 1) // 2))
        for i in range(n):
            plants.append({
                "id": f"physical-{i}",
                "species": "grass",
                "stage": "budding" if i < 2 else "evergreen",
                "health": 1.0,
                "entity_path": "",
                "seed": _stable_seed(f"physical-{i}"),
                "label": f"activity {i + 1}",
                "days_since": 0,
            })
    except Exception as e:
        log.debug("read_physical failed: %s", e)
    return {"slug": "physical", "species": "grass", "plants": plants}


async def read_social(app, window_days: int = 90) -> dict[str, Any]:
    """Wildflower plot — one flower per `person` note. Stage from
    last_contact frontmatter."""
    plants: list[dict] = []
    try:
        people = app.vault_query(tags=["person"]) or []
        for p in people[:12]:
            path = p.get("path") or ""
            fm = p.get("properties", {}) or {}
            name = fm.get("name") or p.get("name") or path.split("/")[-1].removesuffix(".md")
            # last_contact frontmatter wins; else fall back to file mtime.
            stage, days = classify_stage(
                fm.get("last_contact") or fm.get("last_seen") or p.get("modified"),
                created=fm.get("created"),
                path=path,
            )
            plants.append({
                "id": path or f"person-{name}",
                "species": "wildflower",
                "stage": stage,
                "health": 1.0,
                "entity_path": path,
                "seed": _stable_seed(path or name),
                "label": str(name)[:40],
                "days_since": days,
            })
    except Exception as e:
        log.debug("read_social failed: %s", e)
    return {"slug": "social", "species": "wildflower", "plants": plants}


async def read_intellectual(app, window_days: int = 90) -> dict[str, Any]:
    """Sapling plot — one sapling per `kb` note. Stage from note mtime."""
    plants: list[dict] = []
    try:
        notes = app.vault_query(tags=["kb"]) or []
        for n in notes[:10]:
            path = n.get("path") or ""
            fm = n.get("properties", {}) or {}
            title = fm.get("title") or n.get("name") or path.split("/")[-1].removesuffix(".md")
            stage, days = classify_stage(
                fm.get("updated") or fm.get("modified") or n.get("modified"),
                created=fm.get("created"),
                path=path,
            )
            plants.append({
                "id": path or f"kb-{title}",
                "species": "sapling",
                "stage": stage,
                "health": 1.0,
                "entity_path": path,
                "seed": _stable_seed(path or title),
                "label": str(title)[:40],
                "days_since": days,
            })
    except Exception as e:
        log.debug("read_intellectual failed: %s", e)
    return {"slug": "intellectual", "species": "sapling", "plants": plants}


def _stage_from_stale(stale_days: int, status: str) -> tuple[str, int]:
    """Map a project's stale_days + status → (stage, days)."""
    status = (status or "").lower()
    if status in ("completed", "done", "archived", "abandoned", "shipped"):
        return ("dormant", stale_days)
    if status in ("idea",):
        return ("seedling", stale_days)
    if status in ("stalled", "blocked", "paused"):
        return ("wilting", stale_days)
    # active project — stage by recency of activity
    if stale_days < 7:
        return ("budding", stale_days)
    if stale_days < 30:
        return ("evergreen", stale_days)
    if stale_days < 60:
        return ("wilting", stale_days)
    return ("dormant", stale_days)


async def read_occupational(app, window_days: int = 60) -> dict[str, Any]:
    """Bamboo plot — one stalk per active project. Stage from stale_days.

    Calls `projects.list_projects` (the real method behind /projects/api/projects).
    Drops completed/archived projects so the plot shows live work, not history.
    """
    plants: list[dict] = []
    try:
        projs = await app.call_app("projects", "list_projects") or []
        if not isinstance(projs, list):
            projs = []
        # Keep open work; drop finished/idea-only to keep the plot meaningful.
        open_statuses = {"active", "planning", "stalled", "blocked", "paused", ""}
        for p in projs:
            status = (p.get("status") or "").lower()
            if status not in open_statuses:
                continue
            path = p.get("file") or ""
            title = p.get("name") or p.get("id") or "project"
            stale = int(p.get("stale_days") or 0)
            stage, days = _stage_from_stale(stale, status)
            plants.append({
                "id": path or str(p.get("id") or title),
                "species": "bamboo",
                "stage": stage,
                "health": 1.0,
                "entity_path": path,
                "seed": _stable_seed(path or title),
                "label": str(title)[:40],
                "days_since": days,
            })
            if len(plants) >= 10:
                break
    except Exception as e:
        log.warning("read_occupational failed: %s", e)
    return {"slug": "occupational", "species": "bamboo", "plants": plants}


# Plot order is stable — drives grid layout. Internal slugs only; the UI
# layer translates to abstract numerals (no dimension names per CLAUDE.md #16).
READERS = (
    ("physical", read_physical),
    ("social", read_social),
    ("intellectual", read_intellectual),
    ("occupational", read_occupational),
)


async def read_all(app, window_days: int = 30) -> dict[str, dict]:
    """Read every plot in V1. Each entry has shape {slug, species, plants}.

    Per-plot failures degrade silently to empty plots — the garden keeps
    rendering even if a source app is uninstalled or returns the wrong shape.
    """
    out: dict[str, dict] = {}
    for slug, fn in READERS:
        try:
            data = await fn(app, window_days)
        except Exception as e:
            log.warning("plot %s reader crashed: %s", slug, e)
            data = {"slug": slug, "species": "grass", "plants": []}
        data.setdefault("slug", slug)
        data.setdefault("species", "grass")
        data.setdefault("plants", [])
        out[slug] = data
    return out
