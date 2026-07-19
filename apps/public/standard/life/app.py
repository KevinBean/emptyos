"""Life — the Life suite's surface app (pilot of the suite/surface layer).

One integrated day timeline composed from member atom apps. This app owns NO
data: members declare `[[contributes.life.timeline]]` in their manifests and
implement `timeline_items(days) -> list[{ts, title, kind, href, ...}]`; this
app collects via the generic contribution mechanism (fail-soft, zero hard
member deps) and renders the merged timeline. Contract + design rationale:
docs/suites/life-cohesion.md; catalog: suites.toml.

Dark by default — `[apps.life] feature.enabled = true` to light up. With the
flag off every endpoint answers `{enabled: false}` and the page renders a
disabled note; member apps are byte-identical either way.
"""

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.utils import clamp_days


class LifeApp(BaseApp):

    def _enabled(self) -> bool:
        return bool(self.app_config("feature.enabled", False))

    async def day_timeline(self, days: int = 1) -> dict:
        """Merged, ts-descending timeline from every life.timeline contributor.

        Returns {enabled, items, sources}; sources names each contributor and
        its item count so the UI can show provenance and gaps.
        """
        if not self._enabled():
            return {"enabled": False, "items": [], "sources": []}
        items: list[dict] = []
        sources: list[dict] = []
        for entry, result in await self.call_contributions("life", "timeline", days=days):
            if not isinstance(result, list):
                continue
            good = [i for i in result if isinstance(i, dict) and i.get("ts") and i.get("title")]
            sources.append({
                "app": entry.get("_app_id", ""),
                "id": entry.get("id", ""),
                "count": len(good),
            })
            items.extend(good)
        items.sort(key=lambda x: str(x.get("ts", "")), reverse=True)
        return {"enabled": True, "items": items, "sources": sources}

    @web_route("GET", "/api/timeline")
    async def api_timeline(self, request):
        return await self.day_timeline(days=clamp_days(request.query_params.get("days")))
