"""Infinite canvas app — note cards on a board, edges, AI-spawned children.

Decomposed:
- ``storage.py`` — board file codec (split_sections, decode_body, encode_board_file)
- ``prompts.py`` — system prompts for brainstorm/critique/next_steps
- ``layout.py``  — pure node-placement helpers (below_cluster, column_right_of)

This file owns lifecycle, IO (read/write of board files), web routes, hub
panels, and the orchestration that ties think/search results back into
new nodes + edges.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, parse_frontmatter, strip_frontmatter, web_route
from emptyos.sdk.utils import parse_llm_json

from . import layout, storage
from .prompts import BRAINSTORM_SYSTEM, NODE_PROMPTS

_CHECKBOX_RE = re.compile(r"^\s*-\s*\[\s*\]\s*(.+)$")


class CanvasApp(BaseApp):
    # ── paths ────────────────────────────────────────────────────────────

    def _boards_dir(self) -> Path:
        p = self.vault_config_path("boards_dir")
        if p:
            return p
        return self.kernel.config.data_dir / "notes" / "canvas"

    def _board_path(self, board_id: str) -> Path:
        safe = (
            "".join(c for c in (board_id or "inbox") if c.isalnum() or c in ("-", "_")) or "inbox"
        )
        return self._boards_dir() / f"{safe}.md"

    def _vault_rel_path(self, p: str) -> str:
        """Normalize an input path to a vault-relative path (forward slashes)."""
        p = str(p or "").strip().replace("\\", "/")
        if not p:
            return ""
        vault_root = str(self.kernel.config.notes_path).replace("\\", "/").rstrip("/")
        if vault_root and p.startswith(vault_root + "/"):
            p = p[len(vault_root) + 1 :]
        return p.lstrip("/")

    # ── load / save ──────────────────────────────────────────────────────

    async def load_board(self, board_id: str) -> dict:
        path = self._board_path(board_id)
        if not path.exists():
            return {"board_id": board_id, "nodes": [], "edges": [], "meta": {}}
        content = await self.read(str(path))
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content).strip()
        decoded = storage.decode_body(body)
        if decoded is not None:
            nodes, edges = decoded["nodes"], decoded["edges"]
        else:
            # Legacy: body was a single JSON `{nodes: [...], edges: [...]}`
            try:
                data = json.loads(body or "{}")
                nodes = data.get("nodes", []) or []
                edges = data.get("edges", []) or []
            except Exception:
                nodes, edges = [], []
        return {
            "board_id": board_id,
            "meta": fm,
            "nodes": nodes,
            "edges": edges,
            "updated": str(fm.get("updated") or ""),
        }

    async def save_board(
        self, board_id: str, nodes: list, edges: list, meta: dict | None = None
    ) -> dict:
        path = self._board_path(board_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        updated = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        # Preserve passthrough frontmatter (e.g. `project` link from promote_to_project).
        merged_meta: dict = {}
        if path.exists():
            try:
                existing = await self.read(str(path))
                for k, v in (parse_frontmatter(existing) or {}).items():
                    if k not in storage.RESERVED_FM:
                        merged_meta[k] = v
            except Exception:
                pass
        if meta:
            merged_meta.update(meta)

        content = storage.encode_board_file(board_id, nodes, edges, merged_meta, updated)
        await self.write(str(path), content)
        await self.emit(
            "canvas:board_saved",
            {
                "board_id": board_id,
                "nodes": len(nodes),
                "edges": len(edges),
            },
        )
        return {
            "ok": True,
            "path": str(path),
            "board_id": board_id,
            "updated": updated,
            "nodes": nodes,
            "edges": edges,
        }

    # ── add nodes ────────────────────────────────────────────────────────

    async def add_node(
        self, board_id: str, text: str, color: str = "default", source: str = ""
    ) -> dict:
        """Append a text node below the existing cluster."""
        board_id = (board_id or "inbox").strip() or "inbox"
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "empty text"}

        board = await self.load_board(board_id)
        nodes = list(board.get("nodes") or [])
        edges = list(board.get("edges") or [])
        x, y = layout.below_cluster(nodes)

        node_id = secrets.token_hex(5)
        new_node = {
            "id": node_id,
            "x": x,
            "y": y,
            "width": 250,
            "height": 200,
            "text": text,
            "color": color or "default",
        }
        if source:
            new_node["provenance"] = {"mode": "user", "provider": source, "model": ""}
        nodes.append(new_node)

        await self.save_board(board_id, nodes, edges)
        await self.emit(
            "canvas:node_added",
            {
                "board_id": board_id,
                "node_id": node_id,
                "source": source or "api",
            },
        )
        return {"ok": True, "board_id": board_id, "node_id": node_id}

    @web_route("POST", "/api/node")
    async def api_add_node(self, request):
        data = await request.json()
        return await self.add_node(
            board_id=str(data.get("board_id") or "inbox"),
            text=str(data.get("text") or ""),
            color=str(data.get("color") or "default"),
            source=str(data.get("source") or ""),
        )

    async def add_vault_node(self, board_id: str, path: str) -> dict:
        """Append a ``vault_note`` node linking to a vault-relative ``path``."""
        board_id = (board_id or "inbox").strip() or "inbox"
        path = self._vault_rel_path(path)
        if not path:
            return {"ok": False, "error": "empty path"}

        board = await self.load_board(board_id)
        nodes = list(board.get("nodes") or [])
        edges = list(board.get("edges") or [])
        x, y = layout.below_cluster(nodes)

        node_id = secrets.token_hex(5)
        nodes.append(
            {
                "id": node_id,
                "type": "vault_note",
                "path": path,
                "x": x,
                "y": y,
                "width": 300,
                "height": 180,
                "text": "",
                "color": "default",
            }
        )
        await self.save_board(board_id, nodes, edges)
        await self.emit(
            "canvas:node_added",
            {
                "board_id": board_id,
                "node_id": node_id,
                "source": "vault",
                "path": path,
            },
        )
        return {"ok": True, "board_id": board_id, "node_id": node_id, "path": path}

    @web_route("POST", "/api/node/vault")
    async def api_add_vault_node(self, request):
        data = await request.json()
        return await self.add_vault_node(
            board_id=str(data.get("board_id") or "inbox"),
            path=str(data.get("path") or ""),
        )

    @web_route("GET", "/api/vault-preview")
    async def api_vault_preview(self, request):
        """Return ``{title, section, body}`` for a vault-note node to render."""
        path = self._vault_rel_path(request.query_params.get("path", ""))
        if not path:
            return {"ok": False, "error": "path required"}
        vault = Path(self.kernel.config.notes_path)
        abs_path = vault / path
        if not abs_path.exists():
            return {"ok": False, "error": "not found", "path": path}
        try:
            content = await self.read(str(abs_path))
        except Exception as e:
            return {"ok": False, "error": f"read failed: {e}", "path": path}
        body = strip_frontmatter(content).strip()
        title = Path(path).stem.replace("-", " ")
        sections = storage.split_sections(body)
        section_name, first_section = ("", "")
        if sections:
            section_name, first_section = next(iter(sections.items()))
        preview = first_section.strip() if first_section else body[:500]
        return {
            "ok": True,
            "path": path,
            "title": title,
            "section": section_name,
            "body": preview[:800],
        }

    # ── AI children (think + search) ─────────────────────────────────────

    async def _spawn_children(
        self,
        board_id: str,
        node_id: str,
        items: list[dict],
        connect_side: str = "right",
    ) -> list[str]:
        """Materialize child nodes to the right of ``node_id`` and edge-connect them.

        Each item in ``items`` is the full node dict minus position/id/edge —
        callers pre-fill ``text``, ``color``, optional ``provenance``, ``type``,
        ``path``, ``width``/``height``.
        """
        board = await self.load_board(board_id)
        nodes = list(board.get("nodes") or [])
        edges = list(board.get("edges") or [])
        src = next((n for n in nodes if str(n.get("id")) == str(node_id)), None)
        if src is None:
            return []
        positions = layout.column_right_of(src, len(items))
        created: list[str] = []
        for (x, y), item in zip(positions, items, strict=False):
            nid = secrets.token_hex(5)
            node = {
                "id": nid,
                "x": x,
                "y": y,
                "width": item.get("width", 250),
                "height": item.get("height", 200),
                "text": str(item.get("text") or ""),
                "color": item.get("color", "default"),
            }
            for k in ("type", "path", "provenance"):
                if item.get(k):
                    node[k] = item[k]
            nodes.append(node)
            edges.append(
                {
                    "id": secrets.token_hex(5),
                    "sourceId": str(node_id),
                    "sourceSide": connect_side,
                    "targetId": nid,
                    "targetSide": "left",
                }
            )
            created.append(nid)
        await self.save_board(board_id, nodes, edges)
        return created

    @web_route("POST", "/api/node/think")
    async def api_node_think(self, request):
        """Run a think-family prompt against a node's text, spawn children."""
        data = await request.json()
        board_id = str(data.get("board_id") or "inbox")
        node_id = str(data.get("node_id") or "")
        kind = str(data.get("prompt_kind") or "brainstorm")
        if kind not in NODE_PROMPTS:
            return {"ok": False, "error": f"unknown prompt_kind: {kind}"}
        system, expected_n, color = NODE_PROMPTS[kind]

        board = await self.load_board(board_id)
        src = next((n for n in (board.get("nodes") or []) if str(n.get("id")) == node_id), None)
        if src is None:
            return {"ok": False, "error": "node not found"}
        text = str(src.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "node has no text"}

        response = await self.think(
            f"Concept: {text}",
            system=system,
            domain="text",
            temperature=0.7,
        )
        try:
            ideas = parse_llm_json(response, fallback=[])
            if not isinstance(ideas, list):
                ideas = []
        except Exception:
            ideas = []
        ideas = [str(i) for i in ideas[:expected_n]]

        provenance = self.last_provenance() or {}
        prov_payload = None
        if provenance.get("provider"):
            prov_payload = {
                "mode": provenance.get("mode") or "",
                "provider": provenance.get("provider") or "",
                "model": provenance.get("model") or "",
            }
        items = [{"text": idea, "color": color, "provenance": prov_payload} for idea in ideas]
        created = await self._spawn_children(board_id, node_id, items)
        return {
            "ok": True,
            "kind": kind,
            "node_ids": created,
            "ideas": ideas,
            "provenance": provenance,
        }

    @web_route("POST", "/api/node/search")
    async def api_node_search(self, request):
        """Search the vault for a node's text, spawn up to 5 vault-note children."""
        data = await request.json()
        board_id = str(data.get("board_id") or "inbox")
        node_id = str(data.get("node_id") or "")

        board = await self.load_board(board_id)
        src = next((n for n in (board.get("nodes") or []) if str(n.get("id")) == node_id), None)
        if src is None:
            return {"ok": False, "error": "node not found"}
        query = str(src.get("text") or "").strip()
        if not query:
            return {"ok": False, "error": "node has no text"}
        # Multi-line text pollutes the grep query — first line only, capped.
        query = query.split("\n", 1)[0][:120]
        try:
            hits = await self.search(query)
        except Exception as e:
            return {"ok": False, "error": f"search failed: {e}"}

        seen: set[str] = set()
        rel_paths: list[str] = []
        for h in hits or []:
            raw = h.get("path") if isinstance(h, dict) else str(h)
            rel = self._vault_rel_path(raw or "")
            if not rel or rel in seen:
                continue
            seen.add(rel)
            rel_paths.append(rel)
            if len(rel_paths) >= 5:
                break

        if not rel_paths:
            return {"ok": True, "node_ids": [], "paths": [], "count": 0}

        items = [
            {"type": "vault_note", "path": rel, "text": "", "width": 300, "height": 180}
            for rel in rel_paths
        ]
        created = await self._spawn_children(board_id, node_id, items)
        return {"ok": True, "node_ids": created, "paths": rel_paths, "count": len(created)}

    # ── reads ────────────────────────────────────────────────────────────

    async def list_nodes(self, board_id: str) -> list[dict]:
        """Compact ``[{id, text, color, x, y}]`` view of a board (agent-facing)."""
        board = await self.load_board(board_id)
        return [
            {
                "id": n.get("id"),
                "text": str(n.get("text") or ""),
                "color": n.get("color", "default"),
                "x": n.get("x", 0),
                "y": n.get("y", 0),
            }
            for n in (board.get("nodes") or [])
        ]

    @web_route("GET", "/api/nodes")
    async def api_list_nodes(self, request):
        board_id = request.query_params.get("board", "inbox")
        return {"board_id": board_id, "nodes": await self.list_nodes(board_id)}

    async def list_boards(self) -> list[dict]:
        """Scan the boards directory; return summaries sorted by last update."""
        boards_dir = self._boards_dir()
        if not boards_dir.exists():
            return []
        out = []
        for p in boards_dir.glob("*.md"):
            board_id = p.stem
            updated = ""
            node_count = 0
            edge_count = 0
            try:
                content = await self.read(str(p))
                fm = parse_frontmatter(content) or {}
                updated = str(fm.get("updated") or "")
                # Indexed FM counts are cheap; fall back to body-parse on miss.
                try:
                    node_count = int(fm.get("node_count") or 0)
                    edge_count = int(fm.get("edge_count") or 0)
                except (TypeError, ValueError):
                    node_count = edge_count = 0
                if not node_count and not edge_count:
                    body = strip_frontmatter(content).strip()
                    decoded = storage.decode_body(body)
                    if decoded is not None:
                        node_count = len(decoded["nodes"])
                        edge_count = len(decoded["edges"])
                    else:
                        try:
                            data = json.loads(body or "{}")
                            node_count = len(data.get("nodes", []) or [])
                            edge_count = len(data.get("edges", []) or [])
                        except Exception:
                            pass
            except Exception:
                pass
            out.append(
                {
                    "board_id": board_id,
                    "updated": updated,
                    "node_count": node_count,
                    "edge_count": edge_count,
                }
            )
        out.sort(key=lambda b: b.get("updated") or "", reverse=True)
        return out

    @web_route("GET", "/api/board")
    async def api_board(self, request):
        return await self.load_board(request.query_params.get("board", "inbox"))

    @web_route("GET", "/api/boards")
    async def api_boards(self, request):
        return {"boards": await self.list_boards()}

    @web_route("POST", "/api/board")
    async def api_save_board(self, request):
        data = await request.json()
        board_id = str(data.get("board_id", "inbox")).strip() or "inbox"
        return await self.save_board(
            board_id,
            data.get("nodes", []),
            data.get("edges", []),
            data.get("meta", {}),
        )

    @web_route("POST", "/api/board/delete")
    async def api_delete_board(self, request):
        data = await request.json()
        board_id = str(data.get("board_id", "")).strip()
        if not board_id or board_id == "inbox":
            return {"ok": False, "error": "cannot delete inbox or empty board_id"}
        path = self._board_path(board_id)
        if path.exists():
            path.unlink()
            await self.emit("canvas:board_deleted", {"board_id": board_id})
            return {"ok": True}
        return {"ok": False, "error": "not found"}

    # ── connect + promote ────────────────────────────────────────────────

    async def connect(
        self,
        board_id: str,
        source_id: str,
        target_id: str,
        source_side: str = "right",
        target_side: str = "left",
    ) -> dict:
        """Add an edge. Idempotent on (source, target, sides)."""
        board = await self.load_board(board_id)
        nodes = board.get("nodes") or []
        ids = {str(n.get("id")) for n in nodes}
        if str(source_id) not in ids or str(target_id) not in ids:
            return {"ok": False, "error": "unknown node id"}
        edges = list(board.get("edges") or [])
        for e in edges:
            if (
                str(e.get("sourceId")) == str(source_id)
                and str(e.get("targetId")) == str(target_id)
                and str(e.get("sourceSide")) == source_side
                and str(e.get("targetSide")) == target_side
            ):
                return {"ok": True, "edge_id": e.get("id"), "existing": True}
        edge_id = secrets.token_hex(5)
        edges.append(
            {
                "id": edge_id,
                "sourceId": source_id,
                "sourceSide": source_side,
                "targetId": target_id,
                "targetSide": target_side,
            }
        )
        await self.save_board(board_id, nodes, edges)
        return {"ok": True, "edge_id": edge_id, "existing": False}

    @web_route("POST", "/api/connect")
    async def api_connect(self, request):
        data = await request.json()
        return await self.connect(
            board_id=str(data.get("board_id") or "inbox"),
            source_id=str(data.get("source_id") or ""),
            target_id=str(data.get("target_id") or ""),
            source_side=str(data.get("source_side") or "right"),
            target_side=str(data.get("target_side") or "left"),
        )

    async def promote_to_project(
        self,
        board_id: str,
        project_id: str,
        node_ids: list[str] | None = None,
    ) -> dict:
        """Promote nodes to project tasks. Without ``node_ids``, picks the largest cluster."""
        board = await self.load_board(board_id)
        nodes = board.get("nodes") or []
        edges = board.get("edges") or []
        if not nodes:
            return {"ok": False, "error": "board has no nodes"}

        if node_ids:
            wanted = {str(i) for i in node_ids}
            selected_ids = [str(n.get("id")) for n in nodes if str(n.get("id")) in wanted]
        else:
            selected_ids = _largest_connected_component(nodes, edges)

        if not selected_ids:
            return {"ok": False, "error": "no nodes to promote"}

        # Flatten selected nodes into task lines.
        task_lines: list[str] = []
        id_to_node = {str(n.get("id")): n for n in nodes}
        for nid in selected_ids:
            node = id_to_node.get(nid)
            if not node:
                continue
            text = str(node.get("text") or "").strip()
            if not text:
                continue
            lines = text.split("\n")
            checkbox_hits = [
                m.group(1).strip() for line in lines if (m := _CHECKBOX_RE.match(line))
            ]
            if checkbox_hits:
                task_lines.extend(checkbox_hits)
            else:
                task_lines.append(lines[0][:200])

        if not task_lines:
            return {"ok": False, "error": "selected nodes had no text"}

        created: list[str] = []
        for line in task_lines:
            try:
                await self.call_app(
                    "projects", "add_task_to_project", project_id=project_id, text=line
                )
                created.append(line)
            except Exception:
                pass

        # Bi-directional link: stamp `project` in board frontmatter.
        existing_meta = dict(board.get("meta") or {})
        existing_meta["project"] = project_id
        try:
            await self.save_board(board_id, nodes, edges, meta=existing_meta)
        except Exception:
            pass

        await self.emit(
            "canvas:promoted",
            {
                "board_id": board_id,
                "project_id": project_id,
                "node_count": len(selected_ids),
                "task_count": len(created),
            },
        )
        return {
            "ok": True,
            "project_id": project_id,
            "nodes_promoted": len(selected_ids),
            "tasks_created": len(created),
        }

    @web_route("POST", "/api/promote")
    async def api_promote(self, request):
        data = await request.json()
        return await self.promote_to_project(
            board_id=str(data.get("board_id") or "inbox"),
            project_id=str(data.get("project_id") or ""),
            node_ids=data.get("node_ids"),
        )

    # ── one-shot brainstorm (no node) ────────────────────────────────────

    @web_route("POST", "/api/brainstorm")
    async def api_brainstorm(self, request):
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"ideas": [], "provenance": {}}

        response = await self.think(
            f"Concept: {text}",
            system=BRAINSTORM_SYSTEM,
            domain="text",
            temperature=0.7,
        )
        try:
            ideas = parse_llm_json(response, fallback=[])
            if not isinstance(ideas, list):
                ideas = []
        except Exception:
            ideas = []
        return {
            "ideas": [str(i) for i in ideas[:3]],
            "provenance": self.last_provenance(),
        }

    # ── hub panel ────────────────────────────────────────────────────────

    async def panel_recent_boards(self) -> list[dict] | None:
        boards = await self.list_boards()
        if not boards:
            return None
        chips = []
        for b in boards[:5]:
            board_id = b.get("board_id", "inbox")
            nc = b.get("node_count", 0)
            chips.append(
                {
                    "title": f"{board_id} ({nc})" if nc else board_id,
                    "href": f"/canvas/?board={board_id}",
                }
            )
        return chips or None


def _largest_connected_component(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Union-find over nodes/edges. Returns the largest component's node ids."""
    parent: dict[str, str] = {str(n.get("id")): str(n.get("id")) for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in edges:
        s, t = str(e.get("sourceId")), str(e.get("targetId"))
        if s in parent and t in parent:
            union(s, t)
    buckets: dict[str, list[str]] = {}
    for nid in parent:
        buckets.setdefault(find(nid), []).append(nid)
    return max(buckets.values(), key=len) if buckets else []
