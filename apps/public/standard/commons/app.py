"""Commons — daemon-side bridge to the shared EmptyOS Commons service.

The daemon stays single-user; this app is the ONLY outbound seam. It publishes a
vault note to the commons with a chosen visibility (public / private / shared),
and pulls a read-only view of the notes the user may see. Per CLAUDE.md Rules
18/19, every outbound publish passes the cloud-consent gate + outbound leak-scan
*explicitly* (this is an HTTP publish, not an email — we do not abuse `send`),
and nothing leaves the vault without a deliberate publish call.

Pairs with services/emptyos-commons (the multi-tenant shared layer).
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, web_route

# Provider label shown in the consent modal + audit. Not a registered capability
# provider — we call ensure_consent directly for an outbound HTTP publish.
_CONSENT_PROVIDER = "commons"
_COOKIE = "__Host-commons_session"


def build_payload(path: str, props: dict, body: str, visibility: str) -> dict:
    """Serialize a vault note into a commons publish payload. Pure — unit-tested
    without a kernel. Mirrors the KB note shape so it round-trips."""
    slug = Path(path).stem
    tags = props.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "slug": slug,
        "title": props.get("title") or slug,
        "body": body or "",
        "kind": props.get("kind") or "concept",
        "domain": props.get("domain") or "",
        "tags": list(tags),
        "author": props.get("author") or "user",
        "visibility": visibility,
    }


def outbound_summary(payload: dict) -> str:
    """The text the leak-scanner inspects: everything that would leave the vault."""
    return "\n".join(
        str(payload.get(k, "")) for k in ("title", "body", "domain")
    ) + "\n" + " ".join(payload.get("tags", []))


# Per-note default-visibility SUGGESTION for the publish dialog. Pure. This only
# ever *proposes* a default the user confirms — it never auto-publishes (that
# would be the "for you" anti-pattern: guessing wrong on one private note leaks
# it). Safest default is always `private`.
_PRIVATE_KINDS = {
    "daily", "journal", "person", "job-application", "expense", "finance", "moc",
}
_KB_KINDS = {
    "kb", "concept", "formula", "reference", "clause", "case", "lesson", "pattern", "doc",
}


def suggest_visibility(kind: str, tags) -> str:
    """Propose a default visibility from a note's kind/tags. KB-shaped notes →
    `public` (good commons candidates); personal kinds → `private`; unknown →
    `private` (fail safe). A suggestion, not a decision."""
    marks = {(kind or "").strip().lower()}
    if isinstance(tags, str):
        tags = tags.split(",")
    for t in tags or []:
        marks.add(str(t).strip().lower())
    if marks & _PRIVATE_KINDS:
        return "private"
    if marks & _KB_KINDS:
        return "public"
    return "private"


class CommonsApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._cookie_cache: str | None = None

    # ---- config helpers ----
    def _cfg(self, key: str, default: str = "") -> str:
        """Read config preferring the live settings store (UI-configurable via
        the ⚙ panel) over static emptyos.toml [apps.commons]. Settings panels
        write to setting(); app_config() only sees toml — so check both."""
        val = self.setting(key, None)
        if val is None or val == "":
            val = self.app_config(key, default)
        return val or default

    def _server(self) -> str:
        return (self._cfg("commons.server_url", "") or "").rstrip("/")

    async def _ensure_cookie(self) -> str | None:
        """Return a full `name=value` Cookie pair, or None. A pasted
        session_cookie uses the production cookie name; dev-login captures
        whatever name the server set (it differs between https `__Host-` and
        insecure local dev)."""
        token = (self._cfg("commons.session_cookie", "") or "").strip()
        if token:
            return f"{_COOKIE}={token}"
        if self._cookie_cache:
            return self._cookie_cache
        dev = (self._cfg("commons.dev_login", "") or "").strip()
        server = self._server()
        if not dev or not server:
            return None
        import aiohttp

        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{server}/auth/session",
                headers={"Origin": server, "Authorization": f"Bearer {dev}"},
            ) as r:
                if r.status != 200:
                    return None
                # capture the session cookie by its set name (e.g. __Host-commons_session
                # over https, commons_session in insecure local dev).
                for morsel in r.cookies.values():
                    if morsel.key.endswith("commons_session"):
                        self._cookie_cache = f"{morsel.key}={morsel.value}"
                        return self._cookie_cache
        return None

    async def _auth_header(self) -> dict | None:
        """Auth for outbound commons calls. Prefer a durable API token (never
        expires); fall back to a session cookie / dev-login. None = unconfigured."""
        token = (self._cfg("commons.api_token", "") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
        cookie = await self._ensure_cookie()
        if cookie:
            return {"Cookie": cookie}  # already a full name=value pair
        return None

    async def _request(self, method: str, path: str, *, auth: dict, json=None):
        import aiohttp

        server = self._server()
        headers = {"Origin": server, **auth}
        async with aiohttp.ClientSession() as s:
            async with s.request(method, f"{server}{path}", headers=headers, json=json) as r:
                try:
                    data = await r.json()
                except Exception:
                    data = {"detail": await r.text()}
                return r.status, data

    # ---- verbs ----
    async def publish(self, path: str, visibility: str = "private", share_with=None) -> dict:
        """Publish a vault note to the commons. `path` is vault-relative.

        Outbound: explicit consent gate + leak-scan, then a direct HTTP POST.
        Returns {"error": ...} (and sends nothing) when unconfigured or denied.
        """
        if not self._server():
            return {"error": "commons.server_url not configured"}
        if visibility not in ("public", "private", "shared"):
            return {"error": "visibility must be public | private | shared"}
        props = self.vault_get_properties(path) or {}
        # VaultIndex pops `tags` out of properties into a separate field, so
        # vault_get_properties never carries them — pull them back in for publish.
        if not props.get("tags"):
            props = {**props, "tags": self.vault_tags(path)}
        body = self.vault_read_body(path) or ""
        if not body and not props:
            return {"error": f"note not found: {path}"}
        payload = build_payload(path, props, body, visibility)
        if share_with:
            payload["share_with"] = list(share_with)

        # Explicit cloud-consent gate + outbound leak-scan (Rules 18/19).
        if not await self._consent_to_send(payload, visibility):
            return {"error": "publish denied at the consent gate", "sent": False}

        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth — set commons.api_token (or session_cookie / dev_login)"}
        status, data = await self._request("POST", "/api/notes", auth=auth, json=payload)
        if status != 200:
            return {"error": f"commons rejected publish ({status})", "detail": data}
        await self.emit("commons:published", {"slug": payload["slug"], "visibility": visibility})
        self._write_published_hint(path, visibility)
        return {"ok": True, "note": data.get("note"), "shared": data.get("shared", []),
                "unresolved": data.get("unresolved", [])}

    def _mark_enabled(self) -> bool:
        v = self.setting("commons.mark_published", None)
        if v is None:
            v = self.app_config("commons.mark_published", True)
        return str(v).strip().lower() not in ("false", "0", "no", "off")

    def _write_published_hint(self, path: str, visibility: str) -> None:
        """Write a `commons:` frontmatter LABEL on the local note (a hint so the
        vault shows what's shared) — NOT the source of truth; the commons DB is.
        Opt-out via commons.mark_published. Best-effort: publish already succeeded."""
        if not self._mark_enabled():
            return
        from datetime import UTC, datetime

        try:
            self.vault_update(path, {
                "commons": visibility,
                "commons_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        except Exception:
            pass

    def _clear_published_hint(self, path: str) -> None:
        try:
            self.vault_update(path, {"commons": "", "commons_at": ""})
        except Exception:
            pass

    async def _consent_to_send(self, payload: dict, visibility: str) -> bool:
        """Run the outbound leak-scan + cloud-consent gate for this publish.
        Returns True if the user (or policy) allows the bytes to leave."""
        manager = getattr(self.kernel, "cloud_consent", None)
        if manager is None:
            return True  # bare kernel / tests — no gate registered
        try:
            from emptyos.capabilities.outbound_scan import scan_outbound

            findings = [
                {"pattern": f.pattern_name, "preview": f.preview}
                for f in scan_outbound(outbound_summary(payload))
            ]
        except Exception:
            findings = []
        data_summary = (
            f"Publish '{payload.get('title')}' ({len(payload.get('body', ''))} chars) "
            f"to the commons as {visibility}"
        )
        return await manager.ensure_consent(
            provider=_CONSENT_PROVIDER,
            capability="publish",
            data_summary=data_summary,
            findings=findings,
        )

    async def _resolve_owned(self, path: str, auth: dict) -> tuple[dict | None, dict | None]:
        """Find the caller's own commons note matching this vault path's slug.
        Returns (note, None) or (None, {"error": ...})."""
        slug = Path(path).stem
        status, data = await self._request("GET", "/api/notes", auth=auth)
        if status != 200:
            return None, {"error": f"commons list failed ({status})"}
        mine = next(
            (n for n in data.get("notes", []) if n.get("slug") == slug and n.get("is_owner")),
            None,
        )
        if not mine:
            return None, {"error": f"no owned commons note for slug '{slug}'"}
        return mine, None

    async def revoke(self, path: str) -> dict:
        """Withdraw a previously-published note (matched by slug) from the commons."""
        if not self._server():
            return {"error": "commons.server_url not configured"}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth"}
        slug = Path(path).stem
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return err
        status, _ = await self._request("DELETE", f"/api/notes/{mine['id']}", auth=auth)
        if status != 200:
            return {"error": f"commons revoke failed ({status})"}
        await self.emit("commons:revoked", {"slug": slug})
        self._clear_published_hint(path)
        return {"ok": True, "slug": slug}

    # ---- post-publish management (Phase 4 parity — docs/CLOUD-ARCHITECTURE.md).
    # Thin calls to the service's existing management endpoints; the commons DB
    # stays the source of truth, the vault keeps only the hint label.

    async def _consent_widen(self, summary: str) -> bool:
        """Consent gate for audience-widening ops (share, visibility → public/
        shared). No new bytes leave the vault — the note is already on the
        commons — but WHO can see it changes, which deserves the same explicit
        yes as the original publish. Narrowing ops (unshare, → private) skip it."""
        manager = getattr(self.kernel, "cloud_consent", None)
        if manager is None:
            return True  # bare kernel / tests — no gate registered
        return await manager.ensure_consent(
            provider=_CONSENT_PROVIDER,
            capability="publish",
            data_summary=summary,
            findings=[],
        )

    async def share(self, path: str, principal: str, level: str = "read") -> dict:
        """Grant a commons user (email or subject) access to an owned note."""
        if not self._server():
            return {"error": "commons.server_url not configured"}
        principal = (principal or "").strip()
        if not principal:
            return {"error": "principal (email or subject) is required"}
        if level not in ("read", "comment", "write"):
            return {"error": "level must be read | comment | write"}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth"}
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return err
        if not await self._consent_widen(
            f"Share commons note '{mine.get('title') or mine.get('slug')}' "
            f"with {principal} ({level})"
        ):
            return {"error": "share denied at the consent gate", "sent": False}
        status, data = await self._request(
            "POST", f"/api/notes/{mine['id']}/share", auth=auth,
            json={"principal": principal, "level": level},
        )
        if status != 200:
            return {"error": f"commons share failed ({status})", "detail": data}
        await self.emit("commons:shared", {"slug": mine.get("slug"), "principal": principal,
                                           "level": level})
        return {"ok": True, "slug": mine.get("slug"), "level": data.get("level", level)}

    async def unshare(self, path: str, principal: str) -> dict:
        """Remove a commons user's access to an owned note (narrowing — no gate)."""
        if not self._server():
            return {"error": "commons.server_url not configured"}
        principal = (principal or "").strip()
        if not principal:
            return {"error": "principal (email or subject) is required"}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth"}
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return err
        status, data = await self._request(
            "DELETE", f"/api/notes/{mine['id']}/share", auth=auth,
            json={"principal": principal},
        )
        if status != 200:
            return {"error": f"commons unshare failed ({status})", "detail": data}
        await self.emit("commons:unshared", {"slug": mine.get("slug"), "principal": principal})
        return {"ok": True, "slug": mine.get("slug")}

    async def set_visibility(self, path: str, visibility: str) -> dict:
        """Change an owned note's visibility. Widening (→ public/shared) passes
        the consent gate; narrowing (→ private) doesn't."""
        if not self._server():
            return {"error": "commons.server_url not configured"}
        if visibility not in ("public", "private", "shared"):
            return {"error": "visibility must be public | private | shared"}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth"}
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return err
        if visibility != "private" and not await self._consent_widen(
            f"Make commons note '{mine.get('title') or mine.get('slug')}' {visibility}"
        ):
            return {"error": "visibility change denied at the consent gate", "sent": False}
        status, data = await self._request(
            "PATCH", f"/api/notes/{mine['id']}/visibility", auth=auth,
            json={"visibility": visibility},
        )
        if status != 200:
            return {"error": f"commons visibility change failed ({status})", "detail": data}
        await self.emit("commons:visibility_changed",
                        {"slug": mine.get("slug"), "visibility": visibility})
        self._write_published_hint(path, visibility)
        return {"ok": True, "slug": mine.get("slug"), "visibility": visibility}

    async def sync(self, path: str) -> dict:
        """Push the vault note's current title/body/tags onto the published copy
        (the vault is where the human edits; the commons copy follows). New
        bytes leave the vault, so the full leak-scan + consent gate applies —
        same as publish."""
        if not self._server():
            return {"error": "commons.server_url not configured"}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth"}
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return err
        props = self.vault_get_properties(path) or {}
        if not props.get("tags"):
            props = {**props, "tags": self.vault_tags(path)}
        body = self.vault_read_body(path) or ""
        if not body and not props:
            return {"error": f"note not found: {path}"}
        visibility = mine.get("visibility") or "private"
        payload = build_payload(path, props, body, visibility)
        if not await self._consent_to_send(payload, visibility):
            return {"error": "sync denied at the consent gate", "sent": False}
        status, data = await self._request(
            "PATCH", f"/api/notes/{mine['id']}", auth=auth,
            json={"title": payload["title"], "body": payload["body"],
                  "tags": payload["tags"]},
        )
        if status != 200:
            return {"error": f"commons update failed ({status})", "detail": data}
        await self.emit("commons:synced", {"slug": mine.get("slug")})
        return {"ok": True, "slug": mine.get("slug"), "note": data.get("note")}

    async def acl(self, path: str) -> dict:
        """Read-only view of who an owned note is shared with (from the commons
        DB — the source of truth; the vault label carries no ACL)."""
        if not self._server():
            return {"error": "commons.server_url not configured", "grants": []}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth", "grants": []}
        mine, err = await self._resolve_owned(path, auth)
        if err:
            return {**err, "grants": []}
        status, data = await self._request(
            "GET", f"/api/notes/{mine['id']}/acl", auth=auth,
        )
        if status != 200:
            return {"error": f"commons acl view failed ({status})", "grants": []}
        return {"ok": True, "slug": mine.get("slug"),
                "visibility": data.get("visibility"), "grants": data.get("grants", [])}

    async def subscribe(self, query: str = "") -> dict:
        """Read-only view of commons notes the user may see (own + public + shared)."""
        if not self._server():
            return {"error": "commons.server_url not configured", "notes": []}
        auth = await self._auth_header()
        if auth is None:
            return {"error": "no commons auth", "notes": []}
        status, data = await self._request("GET", "/api/notes", auth=auth)
        if status != 200:
            return {"error": f"commons list failed ({status})", "notes": []}
        notes = data.get("notes", [])
        if query:
            q = query.lower()
            notes = [
                n for n in notes
                if q in (n.get("title") or "").lower()
                or q in " ".join(n.get("tags") or []).lower()
            ]
        return {"ok": True, "notes": notes}

    # ---- web ----
    @web_route("GET", "/api/feed")
    async def api_feed(self, request):
        q = (request.query_params.get("q") or "").strip()
        return await self.subscribe(q)

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        """Propose a default visibility for a note (the publish dialog pre-selects
        it; the user still confirms). Never publishes."""
        path = (request.query_params.get("path") or "").strip()
        if not path:
            return {"suggested": "private"}
        props = self.vault_get_properties(path) or {}
        return {"suggested": suggest_visibility(props.get("kind", ""), self.vault_tags(path))}

    @web_route("POST", "/api/publish")
    async def api_publish(self, request):
        body = await request.json()
        return await self.publish(
            body.get("path", ""),
            body.get("visibility", "private"),
            body.get("share_with"),
        )

    @web_route("POST", "/api/revoke")
    async def api_revoke(self, request):
        body = await request.json()
        return await self.revoke(body.get("path", ""))

    @web_route("POST", "/api/share")
    async def api_share(self, request):
        body = await request.json()
        return await self.share(
            body.get("path", ""), body.get("principal", ""),
            body.get("level", "read"),
        )

    @web_route("POST", "/api/unshare")
    async def api_unshare(self, request):
        body = await request.json()
        return await self.unshare(body.get("path", ""), body.get("principal", ""))

    @web_route("POST", "/api/visibility")
    async def api_visibility(self, request):
        body = await request.json()
        return await self.set_visibility(body.get("path", ""), body.get("visibility", ""))

    @web_route("POST", "/api/sync")
    async def api_sync(self, request):
        body = await request.json()
        return await self.sync(body.get("path", ""))

    @web_route("GET", "/api/acl")
    async def api_acl(self, request):
        path = (request.query_params.get("path") or "").strip()
        if not path:
            return {"error": "path is required", "grants": []}
        return await self.acl(path)
