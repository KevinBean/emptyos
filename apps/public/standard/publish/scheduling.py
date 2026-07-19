"""Publish — scheduled local builds with a human deployment gate.

Due drafts are flipped to ``publish: true`` (reversible/internal), then the
site is rebuilt and a proactive notification asks the user to deploy. This
module never calls ``publish.deploy``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import scheduled, web_route
from emptyos.sdk.utils import set_frontmatter_field

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401


def _scheduled_posts_enabled(self) -> bool:
    live = self.setting("publish.feature.publish-scheduled-posts.enabled", None)
    if live is not None:
        return bool(live)
    return bool(self.app_config("feature.publish-scheduled-posts.enabled", False))


def _publish_at_due(value: str, now: datetime | None = None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    current = now or datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=current.tzinfo)
        return parsed <= current
    except ValueError:
        return False


def _scheduled_for_site(self, site: dict, *, due_only: bool = False) -> list[dict]:
    now = datetime.now().astimezone()
    rows = []
    for item in self.scan(site, include_drafts=True):
        publish_at = item.get("publish_at", "")
        if not item.get("draft") or not publish_at:
            continue
        due = _publish_at_due(publish_at, now)
        if due_only and not due:
            continue
        rows.append(
            {
                "path": item.get("path", ""),
                "relative": item.get("relative", ""),
                "title": item.get("title", ""),
                "publish_at": publish_at,
                "due": due,
                "site": site.get("id", ""),
            }
        )
    return rows


async def _release_due_for_site(self, site: dict) -> dict:
    due = self._scheduled_for_site(site, due_only=True)
    if not due:
        return {"site": site.get("id", ""), "released": 0}

    released: list[dict] = []
    for item in due:
        path = Path(item["path"])
        try:
            async with self.note_lock(path):
                content = await self.read(str(path))
                updated = set_frontmatter_field(content, "publish", "true")
                if updated != content:
                    await self.write(str(path), updated)
            released.append(item)
        except Exception as exc:
            self.log_warn(f"scheduled publish release failed for {path}: {exc}")
            continue

        # The flag flip above IS the release; the folder move only tidies the
        # location. Keep it outside the release try — a mirror failure must
        # never drop an already-published post from `released`, or the site
        # would never rebuild and the post would go live unannounced.
        try:
            self._mirror_post_location(path, True, site)
        except Exception as exc:
            self.log_warn(f"scheduled publish mirror failed for {path}: {exc}")

    if not released:
        return {"site": site.get("id", ""), "released": 0}

    stats = self.build(site)
    await self.emit("publish:built", {**stats, "site": site.get("id", "")})
    titles = "; ".join(item["title"][:80] for item in released[:3])
    digest = hashlib.sha256(
        "|".join(sorted(item["path"] for item in released)).encode("utf-8")
    ).hexdigest()[:12]
    if stats.get("error"):
        await self.proactive_notify_or_raw(
            kind="publish-build-failed",
            text=(
                f"{len(released)} scheduled post{'s were' if len(released) != 1 else ' was'} "
                f"released, but the local build failed: {stats['error']}"
            ),
            dedup_key=f"publish-build-failed:{site.get('id', '')}:{digest}",
            priority="high",
            source="publish",
        )
        return {
            "site": site.get("id", ""),
            "released": len(released),
            "titles": [item["title"] for item in released],
            "build": stats,
            "deploy_required": False,
        }
    await self.proactive_notify_or_raw(
        kind="publish-ready",
        text=(
            f"{len(released)} scheduled post{'s are' if len(released) != 1 else ' is'} "
            f"built and ready to deploy: {titles}"
        ),
        dedup_key=f"publish-ready:{site.get('id', '')}:{digest}",
        priority="info",
        source="publish",
    )
    return {
        "site": site.get("id", ""),
        "released": len(released),
        "titles": [item["title"] for item in released],
        "build": stats,
        "deploy_required": True,
    }


@scheduled("*/15 * * * *", id="publish-scheduled-posts")
async def scheduled_publish_posts(self):
    if not self._scheduled_posts_enabled():
        return {"enabled": False, "released": 0}
    results = []
    for site in self._load_sites():
        if site.get("mode") == "static-mirror":
            continue
        result = await self._release_due_for_site(site)
        if result.get("released"):
            results.append(result)
    return {
        "enabled": True,
        "released": sum(row.get("released", 0) for row in results),
        "sites": results,
        "deploy_required": bool(results),
    }


@web_route("GET", "/api/scheduled")
async def api_scheduled_posts(self, request):
    rows: list[dict] = []
    if self._scheduled_posts_enabled():
        for site in self._load_sites():
            if site.get("mode") != "static-mirror":
                rows.extend(self._scheduled_for_site(site))
    return {"enabled": self._scheduled_posts_enabled(), "posts": rows}
