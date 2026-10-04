"""Notebook — a note-first workspace over the vault: tree, note page, links."""

from __future__ import annotations

import asyncio
import logging
import time

from emptyos.sdk import BaseApp, cli_command, web_route

from . import listing

log = logging.getLogger("emptyos.notebook")

# Backlinks come from the `link` app's index, which it builds on first use and
# after every vault change — measured at 100-345 s on a large vault (see
# link/app.py `on_vault_changed`). A request waits this long, then answers
# `pending` while the lookup keeps running; the page polls. Short enough that a
# request never approaches the HTTP layer's ~30 s limit.
BACKLINKS_WAIT_S = 6.0
# A finished lookup is served from here for this long, so a poll that arrives
# after the result landed gets it instead of starting another full rebuild.
BACKLINKS_KEEP_S = 120.0

INDEX_UNAVAILABLE = "vault index unavailable — is a vault mounted?"


class NotebookApp(BaseApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # path -> ([finished_at] once done, future). Held so a running lookup is not
        # collected mid-flight (a bare ensure_future is only weak-ref'd), and
        # kept after it finishes so a later poll can collect the result.
        self._backlinks: dict[str, tuple[list[float], asyncio.Future]] = {}

    async def teardown(self):
        for _, fut in self._backlinks.values():
            fut.cancel()
        self._backlinks.clear()
        await super().teardown()

    # ── vault view ─────────────────────────────────────────────
    def _note_paths(self) -> list[str] | None:
        """Every indexed note path, or None when there is no index to ask.

        None is distinct from [] on purpose: an absent index must not read as
        an empty vault.
        """
        vi = self.kernel.services.get_optional("vault_index")
        if not vi:
            return None
        return [e["path"] for e in vi.find() if e.get("path")]

    @staticmethod
    def _match(raw: str, paths: list[str]) -> str:
        want = str(raw or "").strip().replace("\\", "/").strip("/")
        if not want:
            return ""
        if not want.lower().endswith(".md"):
            want += ".md"
        return want if want in set(paths) else ""

    async def tree(self, dir: str = "") -> dict:
        folder, err = listing.normalize_dir(dir)
        if err:
            return {"error": err}
        paths = self._note_paths()
        if paths is None:
            return {"error": INDEX_UNAVAILABLE}
        return listing.list_dir(paths, folder)

    async def resolve_title(self, title: str) -> dict:
        paths = self._note_paths()
        if paths is None:
            return {"error": INDEX_UNAVAILABLE, "candidates": []}
        found = listing.resolve(title, *listing.build_lookup(paths))
        if not found:
            return {"error": f"no note named {title!r}", "candidates": []}
        if len(found) > 1:
            return {"error": f"{title!r} names {len(found)} notes", "candidates": found}
        return {"path": found[0]}

    async def outgoing(self, path: str) -> dict:
        paths = self._note_paths()
        if paths is None:
            return {"error": INDEX_UNAVAILABLE}
        rel = self._match(path, paths)
        if not rel:
            return {"error": f"no such note: {path}"}
        try:
            text = await self.read(rel)
        except Exception as e:  # deleted or unreadable since the index saw it
            return {"error": f"could not read {rel}: {e}"}
        links = listing.outgoing_links(text, *listing.build_lookup(paths), source=rel)
        return {"path": rel, "links": links}

    async def backlinks(self, path: str) -> dict:
        paths = self._note_paths()
        if paths is None:
            return {"error": INDEX_UNAVAILABLE}
        rel = self._match(path, paths)
        if not rel:
            return {"error": f"no such note: {path}"}
        now = time.monotonic()
        for k, (fin, f) in list(self._backlinks.items()):
            # Timed from when the lookup FINISHED: a rebuild outlasts the keep
            # window, so timing from the start would discard every slow result.
            if f.done() and fin and now - fin[0] > BACKLINKS_KEEP_S:
                del self._backlinks[k]
        entry = self._backlinks.get(rel)
        if entry is None:
            fut = asyncio.ensure_future(self.try_call_app("link", "backlinks", title=rel))
            finished: list[float] = []
            fut.add_done_callback(lambda _f, fin=finished: fin.append(time.monotonic()))
            self._backlinks[rel] = (finished, fut)
        else:
            fut = entry[1]
        done, _ = await asyncio.wait({fut}, timeout=BACKLINKS_WAIT_S)
        if not done:
            return {"path": rel, "pending": True, "backlinks": []}
        if fut.cancelled():
            self._backlinks.pop(rel, None)
            return {"path": rel, "backlinks": [], "unavailable": "backlink lookup was cancelled"}
        found, err = fut.result()  # try_call_app never raises
        if err:
            self._backlinks.pop(rel, None)  # let the next request retry
            log.info("backlinks unavailable for %s: %s", rel, err)
            return {"path": rel, "backlinks": [], "unavailable": "link index unavailable"}
        return {"path": rel, "backlinks": sorted(p for p in (found or []) if p != rel)}

    async def note_path(self, id: str = "") -> str:
        """Vault-relative path of an indexed note, or "" — the timeline entity source."""
        return self._match(id, self._note_paths() or [])

    # ── Web API ────────────────────────────────────────────────
    @web_route("GET", "/api/tree")
    async def api_tree(self, request):
        return await self.tree(request.query_params.get("dir", ""))

    @web_route("GET", "/api/resolve")
    async def api_resolve(self, request):
        title = request.query_params.get("title", "").strip()
        if not title:
            return {"error": "title is required"}
        return await self.resolve_title(title)

    @web_route("GET", "/api/outgoing")
    async def api_outgoing(self, request):
        path = request.query_params.get("path", "").strip()
        if not path:
            return {"error": "path is required"}
        return await self.outgoing(path)

    @web_route("GET", "/api/backlinks")
    async def api_backlinks(self, request):
        path = request.query_params.get("path", "").strip()
        if not path:
            return {"error": "path is required"}
        return await self.backlinks(path)

    # ── CLI ────────────────────────────────────────────────────
    @cli_command("notebook", help="List a vault folder: eos notebook [dir]")
    async def cmd_notebook(self, dir: str = ""):
        data = await self.tree(dir)
        if data.get("error"):
            self.print_rich(f"[red]{data['error']}[/red]")
            return
        for f in data["folders"]:
            print(f"  {f['name']}/  ({f['count']})")
        for f in data["files"]:
            print(f"  {f['name']}")
