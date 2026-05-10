"""Canvas board file codec — pure string ↔ structure transforms.

A board file looks like:

    ---
    type: canvas
    tags:
      - canvas
    board_id: <id>
    title: <id>
    updated: <iso>
    node_count: N
    edge_count: M
    {extra-frontmatter passthrough}
    ---

    ## _meta

    ```json
    {"layout": {"<nid>": {...}, ...}, "edges": [...]}
    ```

    ## n<nid1>

    text…

The codec is independent of vault IO and the kernel — the app reads/writes
files; this module only encodes and decodes.
"""

from __future__ import annotations

import json
import re
from typing import Any

_META_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

RESERVED_FM = {"type", "tags", "board_id", "title", "updated", "node_count", "edge_count"}


def split_sections(body: str) -> dict[str, str]:
    """Parse ``## <name>`` headers. Returns ``{name: body_text}`` (stripped)."""
    sections: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            if name is not None:
                sections[name] = "\n".join(buf).strip("\n")
            name = line[3:].strip()
            buf = []
        elif name is not None:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf).strip("\n")
    return sections


def decode_body(body: str) -> dict | None:
    """Extract ``{nodes, edges}`` from the section-based body shape.

    Returns ``None`` if the body isn't in the current shape (caller falls
    back to the legacy single-JSON-object reader).
    """
    sections = split_sections(body)
    meta_text = sections.get("_meta") or ""
    m = _META_FENCE_RE.search(meta_text)
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except Exception:
        return None
    layout = meta.get("layout") or {}
    edges_raw = meta.get("edges") or []
    nodes: list[dict] = []
    for nid, lay in layout.items():
        text = sections.get(f"n{nid}", "")
        node = {
            "id": nid,
            "x": lay.get("x", 0),
            "y": lay.get("y", 0),
            "width": lay.get("w", 250),
            "height": lay.get("h", 200),
            "color": lay.get("color", "default"),
            "text": text,
        }
        ntype = lay.get("type")
        if ntype and ntype != "text":
            node["type"] = ntype
        if lay.get("path"):
            node["path"] = lay["path"]
        if lay.get("prov"):
            node["provenance"] = lay["prov"]
        nodes.append(node)
    edges = [
        {
            "id": e.get("id"),
            "sourceId": e.get("source"),
            "sourceSide": e.get("source_side"),
            "targetId": e.get("target"),
            "targetSide": e.get("target_side"),
        }
        for e in edges_raw
    ]
    return {"nodes": nodes, "edges": edges}


def encode_board_file(
    board_id: str,
    nodes: list[dict],
    edges: list[dict],
    extra_meta: dict | None,
    updated: str,
) -> str:
    """Render a complete board file (frontmatter + body) as a single string.

    ``extra_meta`` may carry passthrough frontmatter from previous saves
    (e.g. ``project`` set by ``promote_to_project``). Reserved keys are
    skipped — the canonical values from ``nodes``/``edges``/``updated``
    win.
    """
    layout: dict[str, dict] = {}
    order: list[str] = []
    for n in nodes:
        nid = str(n.get("id") or "").strip()
        if not nid:
            continue
        order.append(nid)
        entry: dict[str, Any] = {
            "x": n.get("x", 0),
            "y": n.get("y", 0),
            "w": n.get("width", 250),
            "h": n.get("height", 200),
            "color": n.get("color", "default"),
        }
        ntype = n.get("type")
        if ntype and ntype != "text":
            entry["type"] = ntype
        if n.get("path"):
            entry["path"] = n["path"]
        if n.get("provenance"):
            entry["prov"] = n["provenance"]
        layout[nid] = entry

    edges_out = [
        {
            "id": e.get("id"),
            "source": e.get("sourceId"),
            "source_side": e.get("sourceSide"),
            "target": e.get("targetId"),
            "target_side": e.get("targetSide"),
        }
        for e in edges
    ]

    fm_lines = [
        "---",
        "type: canvas",
        "tags:",
        "  - canvas",
        f"board_id: {board_id}",
        f"title: {board_id}",
        f"updated: {updated}",
        f"node_count: {len(nodes)}",
        f"edge_count: {len(edges)}",
    ]
    for k, v in (extra_meta or {}).items():
        if k in RESERVED_FM:
            continue
        fm_lines.append(
            f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
        )
    fm_lines.append("---")
    fm_lines.append("")

    meta_json = json.dumps({"layout": layout, "edges": edges_out}, ensure_ascii=False, indent=2)
    body_parts = ["## _meta", "", "```json", meta_json, "```", ""]
    nodes_by_id = {str(n.get("id")): n for n in nodes}
    for nid in order:
        text = str(nodes_by_id[nid].get("text") or "")
        body_parts.extend([f"## n{nid}", "", text, ""])

    return "\n".join(fm_lines) + "\n".join(body_parts) + "\n"
