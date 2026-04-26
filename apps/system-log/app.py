"""System Log — system activity feed.

Aggregates all EventBus events into a readable feed.
Groups by type, shows recent activity, tracks system pulse.
"""

from __future__ import annotations

from collections import Counter

from emptyos.sdk import BaseApp, cli_command, web_route


class SystemLogApp(BaseApp):

    async def recent(self, limit: int = 50) -> list[dict]:
        return await self.kernel.events.history(limit=limit)

    async def summary(self) -> dict:
        events = await self.recent(200)
        type_counts = Counter(e["type"] for e in events)
        source_counts = Counter(e["source"] for e in events)
        return {
            "total_events": len(events),
            "by_type": dict(type_counts.most_common(15)),
            "by_source": dict(source_counts.most_common(10)),
            "latest": events[:5],
        }

    async def add(self, text: str, source: str = "staff") -> dict:
        """Add a system notification to the feed via EventBus."""
        await self.emit("system-log:posted", {"text": text, "source": source})
        self.log_activity({"action": "posted", "text": text[:200], "source": source})
        return {"posted": True, "text": text}

    @web_route("POST", "/api/post")
    async def api_post(self, request):
        data = await request.json()
        return await self.add(data.get("text", ""), data.get("source", "staff"))

    @cli_command("syslog", help="System activity feed")
    async def cmd_syslog(self, action: str = "recent", limit: str = "20"):
        if action == "recent":
            events = await self.recent(int(limit))
            for e in events:
                ts = e["timestamp"][:19].replace("T", " ")
                print(f"  {ts}  {e['type']:<25} {e['source']}")
        elif action == "summary":
            s = await self.summary()
            print(f"\n  {s['total_events']} events")
            print(f"\n  By type:")
            for t, c in s["by_type"].items():
                print(f"    {t:<30} {c}")
            print(f"\n  By source:")
            for src, c in s["by_source"].items():
                print(f"    {src:<20} {c}")
            print()

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        limit = int(request.query_params.get("limit", "50"))
        events = await self.recent(limit)
        source = request.query_params.get("source", "")
        event_type = request.query_params.get("type", "")
        q = request.query_params.get("q", "").lower()
        if source:
            events = [e for e in events if e.get("source") == source]
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        if q:
            events = [e for e in events if q in str(e.get("data", "")).lower() or q in e.get("type", "").lower()]
        return events

    @web_route("GET", "/api/summary")
    async def api_summary(self, request):
        return await self.summary()

    @web_route("GET", "/api/feed")
    async def api_feed(self, request):
        """Events grouped by date for a feed-style view."""
        await self.emit("system-log:feed_viewed", {"source": "web"})
        events = await self.recent(200)
        by_date: dict[str, list] = {}
        for e in events:
            d = e.get("timestamp", "")[:10]
            if d:
                by_date.setdefault(d, []).append(e)
        return {d: items for d, items in sorted(by_date.items(), reverse=True)}

    @web_route("GET", "/api/narrative")
    async def api_narrative(self, request):
        """AI-generated narrative of recent system activity."""
        events = await self.recent(100)
        if not events:
            return {"narrative": "No recent activity.", "events": 0}
        type_counts = Counter(e["type"] for e in events)
        source_counts = Counter(e["source"] for e in events)
        context = (
            f"Last {len(events)} events. "
            f"Top types: {', '.join(f'{t} ({c})' for t, c in type_counts.most_common(8))}. "
            f"Top sources: {', '.join(f'{s} ({c})' for s, c in source_counts.most_common(5))}."
        )
        narrative = await self.think(
            f"Summarize this system activity in 2-3 sentences. "
            f"What's the user been doing? Any notable patterns?\n\n{context}",
            domain="text", temperature=0.5,
        )
        return {"narrative": narrative, "events_analyzed": len(events)}

    @web_route("GET", "/api/sources")
    async def api_sources(self, request):
        """List all event sources with counts."""
        events = await self.recent(500)
        sources = Counter(e["source"] for e in events)
        return [{"source": s, "count": c} for s, c in sources.most_common()]

    # --- Structured System Log (persisted, queryable) ---

    @web_route("GET", "/api/logs")
    async def api_logs(self, request):
        """Query structured system logs. Params: limit, level, source, job_id."""
        limit = int(request.query_params.get("limit", "100"))
        level = request.query_params.get("level", "")
        source = request.query_params.get("source", "")
        job_id = request.query_params.get("job_id", "")
        return self.kernel.syslog.query(limit=limit, level=level, source=source, job_id=job_id)

    @web_route("GET", "/api/logs/errors")
    async def api_log_errors(self, request):
        """Recent errors and warnings."""
        import time
        since = time.time() - 86400  # last 24h
        errors = self.kernel.syslog.query(limit=50, level="error", since=since)
        warns = self.kernel.syslog.query(limit=50, level="warn", since=since)
        return {"errors": errors, "warnings": warns}
