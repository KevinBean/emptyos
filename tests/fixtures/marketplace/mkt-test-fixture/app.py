"""Tiny fixture app for marketplace install tests — see tests/test_sys_store.py."""

from __future__ import annotations

from emptyos.sdk import BaseApp, web_route


class MktTestFixtureApp(BaseApp):
    @web_route("GET", "/api/ping")
    async def api_ping(self, request) -> dict:
        return {"pong": True}
