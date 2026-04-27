"""Chatbot Q&A proxy — forwards admin requests to the standalone chat service.

Functions here are bound onto PublishApp as methods (see app.py wiring section),
so they receive `self` as their first argument and use BaseApp helpers directly.
"""

from pathlib import Path

from emptyos.sdk import web_route

# ── Chatbot Q&A — proxies to the standalone chat service ──────────
#
# The chat service runs separately (services/chatbot/) and owns the qa_log
# SQLite. EmptyOS does not see Q&A content directly — these routes simply
# forward signed requests to the service's /admin endpoints. Configure via:
#
#   [apps.publish.chatbot]
#   admin_token = "..."     # matches CHATBOT_ADMIN_TOKEN on the service side
#   endpoint    = "https://chat.binbian.net"   # optional override; falls
#                                              # back to site.chatbot.endpoint
#
# If a request to the service fails, the route returns the error verbatim
# so the UI can render it. Network errors return 502.

def _chatbot_admin_creds(self, site: dict | None = None) -> tuple[str, str]:
    """Return (endpoint, admin_token) for the active site's chat service.

    Endpoint priority: site.chatbot.endpoint → app_config('chatbot.endpoint').
    Token: app_config('chatbot.admin_token'). Empty token = feature off.
    """
    s = site or self._active_site()
    cb = s.get("chatbot") or {}
    endpoint = (cb.get("endpoint") or "").strip()
    if not endpoint:
        endpoint = self.app_config("chatbot.endpoint", "") or ""
    token = self.app_config("chatbot.admin_token", "") or ""
    return endpoint.rstrip("/"), token

async def _chatbot_admin_request(
    self, method: str, path: str, *,
    site: dict | None = None,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict:
    endpoint, token = self._chatbot_admin_creds(site)
    if not endpoint:
        return {"error": "chatbot endpoint not configured"}
    if not token:
        return {"error": "chatbot.admin_token not set"}
    import aiohttp
    url = endpoint + path
    headers = {"X-Admin-Token": token}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.request(
                method, url, json=json_body, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                body = await r.json(content_type=None)
                if r.status >= 400:
                    return {"error": body.get("error") if isinstance(body, dict) else str(body),
                            "status": r.status}
                return body if isinstance(body, dict) else {"data": body}
    except Exception as e:
        return {"error": f"chat service unreachable: {e}"}

@web_route("GET", "/api/chatbot/qa-log/{site_id}")
async def api_chatbot_qa_list(self, request):
    site_id = request.path_params["site_id"]
    site = self._get_site(site_id)
    if not site:
        return {"error": f"site '{site_id}' not found"}
    status = request.query_params.get("status") or None
    limit = int(request.query_params.get("limit") or 50)
    offset = int(request.query_params.get("offset") or 0)
    params = {"limit": str(limit), "offset": str(offset)}
    if status:
        params["status"] = status
    return await self._chatbot_admin_request(
        "GET", f"/admin/qa-log/{site_id}", site=site, params=params
    )

@web_route("POST", "/api/chatbot/qa-log/{site_id}/{qa_id}")
async def api_chatbot_qa_update(self, request):
    site_id = request.path_params["site_id"]
    qa_id = request.path_params["qa_id"]
    site = self._get_site(site_id)
    if not site:
        return {"error": f"site '{site_id}' not found"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await self._chatbot_admin_request(
        "POST", f"/admin/qa-log/{qa_id}", site=site, json_body=body,
    )

@web_route("POST", "/api/chatbot/qa-log/{site_id}/{qa_id}/promote")
async def api_chatbot_qa_promote(self, request):
    """Promote a curated Q&A to the canonical FAQ — appends to faqs.toml."""
    site_id = request.path_params["site_id"]
    qa_id = request.path_params["qa_id"]
    site = self._get_site(site_id)
    if not site:
        return {"error": f"site '{site_id}' not found"}
    # Ask service for the q/a payload (this also marks the row curated).
    result = await self._chatbot_admin_request(
        "POST", f"/admin/qa-log/{qa_id}/promote", site=site,
    )
    if "error" in result:
        return result
    q = result.get("q") or ""
    a = result.get("a") or ""
    if not q or not a:
        return {"error": "service returned empty q/a"}
    try:
        written_to = self._append_faq(site, q, a)
    except Exception as e:
        return {"error": f"failed to write faqs.toml: {e}"}
    return {"ok": True, "id": qa_id, "written_to": written_to, "q": q}

@web_route("GET", "/api/chatbot/faqs/{site_id}")
async def api_chatbot_faqs_list(self, request):
    """Read the site's faqs.toml from vault — used by the UI to show canon."""
    site_id = request.path_params["site_id"]
    site = self._get_site(site_id)
    if not site:
        return {"error": f"site '{site_id}' not found"}
    faqs = self._read_faqs(site)
    return {"faqs": faqs, "path": str(self._faqs_path(site))}

def _faqs_path(self, site: dict) -> Path:
    """Where faqs.toml lives for a site — alongside the published-source folder."""
    vault = self._vault_dir()
    source = self._source_folder(site)
    return Path(vault) / source / "faqs.toml"

def _read_faqs(self, site: dict) -> list[dict]:
    import tomllib
    p = self._faqs_path(site)
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []
    raw = data.get("faq") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        {"q": str(it.get("q") or "").strip(), "a": str(it.get("a") or "").strip()}
        for it in raw if isinstance(it, dict) and it.get("q") and it.get("a")
    ]

def _append_faq(self, site: dict, q: str, a: str) -> str:
    """Append {q, a} to {source}/faqs.toml. Creates the file if missing.

    Returns the absolute path written. Idempotent on the q-string: if a
    row with the same `q` already exists, replaces its `a` instead of
    adding a duplicate.
    """
    path = self._faqs_path(site)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = self._read_faqs(site)
    # Replace-if-q-matches; otherwise append.
    replaced = False
    for entry in existing:
        if entry["q"].strip().lower() == q.strip().lower():
            entry["a"] = a
            replaced = True
            break
    if not replaced:
        existing.append({"q": q, "a": a})

    # Write back as TOML. We hand-format because tomllib is read-only
    # and we want stable, human-editable output.
    def _toml_escape(s: str) -> str:
        # Triple-quoted strings need escaping for backslashes + triple quotes.
        return s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')

    lines = [
        "# Hand-written FAQ canon for this site.",
        "# Each entry serves free + instant via the chatbot service before any LLM call.",
        "",
    ]
    for entry in existing:
        lines.append("[[faq]]")
        lines.append(f'q = """{_toml_escape(entry["q"])}"""')
        lines.append(f'a = """{_toml_escape(entry["a"])}"""')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
