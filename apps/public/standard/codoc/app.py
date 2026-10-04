"""Co-doc bridge — persist a live co-doc snapshot into the vault (Phase-4 write-side).

The live co-doc service (Lane 1) has no vault; its ``/api/docs/{id}/snapshot``
endpoint produces an ``author: both`` markdown note. This daemon-side bridge is
the consumer that writes that markdown into the owner's vault — the one durable
record of an otherwise-ephemeral live session. The service formats; the daemon
persists (Lane 1 = no vault, by design).
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.utils import slugify

# Co-doc WS wire protocol (mirrors emptyos_codoc/protocol.py): 1-byte type tag.
_DOC = 0
_AWARENESS = 1


def _slugify(text: str) -> str:
    # Stable note-id slug (no truncation) via the shared SDK helper — matches
    # codoc's prior [^a-z0-9]+ behaviour exactly.
    return slugify(text, max_len=None, fallback="co-doc")


class CodocApp(BaseApp):
    @staticmethod
    def _rev(content: str) -> str:
        """Content fingerprint for the staleness gate — same shape as canvas's
        (`.claude/rules/proposed-action.md`): a hash of the bytes, not the
        `updated:` timestamp (second-resolution, so two writes in the same
        second are indistinguishable — measured there, not assumed)."""
        return hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]

    @web_route("POST", "/api/save")
    async def api_save(self, request):
        """Persist co-doc snapshot markdown into the vault as a note.

        Flagged in gap analysis (codoc-snapshot-overwrite-risk): this used to
        write unconditionally, so a note hand-edited since the co-doc
        service's last snapshot would be silently clobbered. Tracks the
        content-hash of what THIS app last wrote per slug
        (`data/apps/codoc/state.json`); refuses an overwrite when the note on
        disk no longer matches that hash — someone else (a human, another
        app) touched it since our last save. `force: true` bypasses the
        check for a caller that has already reconciled (or a first save for
        a slug we've never written, which always proceeds).
        """
        body = await request.json()
        if not isinstance(body, dict):
            return {"error": "body must be an object"}
        markdown = str(body.get("markdown") or "")
        if not markdown.strip():
            return {"error": "markdown is required"}
        force = bool(body.get("force"))
        slug = _slugify(body.get("slug") or "")
        filename = f"{slug}.md"
        # Defense in depth: _slugify already whitelists [a-z0-9-] (no path
        # separators can survive), but assert the resolved path stays inside this
        # app's vault dir before writing — so a future _slugify change can't open
        # a traversal hole.
        path = self.vault_path(filename)
        try:
            path.resolve().relative_to(self.vault_dir.resolve())
        except ValueError:
            return {"error": "invalid slug"}

        async with self.note_lock(path):
            state = self.load_state({"revisions": {}})
            revisions = state.setdefault("revisions", {})
            known_rev = revisions.get(slug)
            if known_rev and not force and path.exists():
                try:
                    current = await self.read(str(path))
                except Exception:
                    current = ""
                current_rev = self._rev(current)
                if current_rev != known_rev:
                    return {
                        "ok": False,
                        "error": "stale",
                        "slug": slug,
                        "message": (
                            "This note changed since the co-doc's last save — "
                            "someone edited it directly. Pass force:true to "
                            "overwrite, or reconcile the changes first."
                        ),
                    }
            self.vault_write(filename, markdown)
            revisions[slug] = self._rev(markdown)
            self.save_state(state)

        rel = self.vault_rel(path) or filename
        await self.emit("codoc:saved", {"slug": slug, "path": rel})
        return {"ok": True, "slug": slug, "path": rel}

    @web_route("GET", "/api/saved")
    async def api_saved(self, request):
        """List saved co-doc snapshots in this app's vault directory."""
        saved = []
        for f in self.vault_list("*.md"):
            rel = self.vault_rel(f) or f.name
            saved.append({"slug": f.stem, "path": rel, "_vault_path": rel})
        return {"saved": saved}

    async def agent_edit(self, doc_id: str = "", principal: str = "", text: str = "") -> dict:
        """Have an agent join a live co-doc and apply a text edit (Phase-3 wiring).

        The daemon-side agent live co-editor: connect to the co-doc service WS as
        ``agent:<id>`` (the agent must be a write member of the doc), merge the
        bootstrap state, apply a CRDT edit, and send it — the same mechanism a
        human's browser uses, driven programmatically and attributed by principal.

        Flagged in gap analysis (codoc-agent-edit-undiscoverable): this was a
        real, working, carefully-scoped capability reachable only via the raw
        HTTP route, with no `[[provides.verbs]]` registration — so no agent or
        automation could discover or call it. Extracted from `api_agent_edit`
        into this plain-kwargs method (the HTTP route is now a thin wrapper)
        so `call_app("codoc", "agent_edit", ...)` works directly.
        """
        doc_id = str(doc_id or "").strip()
        principal = str(principal or "").strip()
        text = str(text or "")
        if not (doc_id and principal and text):
            return {"error": "doc_id, principal, and text are required"}
        # The single-user daemon IS the owner; it may act as one of the owner's
        # AGENTS, never impersonate a human or arbitrary principal to the co-doc
        # service. Restrict the forwarded X-EOS-Principal to agent:<id>. (codoc's
        # membership ACL is the downstream gate on what that agent may do.)
        if not re.fullmatch(r"agent:[A-Za-z0-9._:-]+", principal):
            return {"error": "principal must be an owned agent (agent:<id>)"}
        service = self.app_config("service_url", "http://127.0.0.1:9400")
        return await self._agent_edit(service, doc_id, principal, text)

    @web_route("POST", "/api/agent-edit")
    async def api_agent_edit(self, request):
        body = await request.json()
        if not isinstance(body, dict):
            return {"error": "body must be an object"}
        return await self.agent_edit(
            doc_id=body.get("doc_id") or "",
            principal=body.get("principal") or "",
            text=body.get("text") or "",
        )

    async def _agent_edit(self, service: str, doc_id: str, principal: str, text: str) -> dict:
        try:
            import aiohttp
            import pycrdt
        except ImportError as e:  # graceful: app loads without the live-edit deps
            return {"error": f"agent co-editor needs pycrdt + aiohttp ({e})"}

        ws_url = (
            service.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
            + f"/api/docs/{doc_id}/sync?principal={principal}"
        )
        doc = pycrdt.Doc()
        doc["t"] = pycrdt.Text()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, timeout=8) as ws:
                    # Bootstrap (a DOC frame) arrives only if the doc has content.
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                        if (msg.type == aiohttp.WSMsgType.BINARY and msg.data
                                and msg.data[0] == _DOC):
                            doc.apply_update(msg.data[1:])
                    except asyncio.TimeoutError:
                        pass  # empty doc — nothing to bootstrap
                    # Apply the agent's edit, capture the diff, send it framed.
                    before = doc.get_state()
                    doc["t"].insert(len(str(doc["t"])), text)
                    diff = doc.get_update(before)
                    await ws.send_bytes(bytes([_DOC]) + diff)
                    await asyncio.sleep(0.3)  # let the server merge + persist
        except Exception as e:  # noqa: BLE001 — surface a clean error to the caller
            return {"error": f"agent edit failed: {str(e)[:200]}"}
        await self.emit("codoc:agent_edited", {"doc_id": doc_id, "principal": principal})
        return {"ok": True, "doc_id": doc_id, "principal": principal, "applied": text}
