"""VaultQuery tool — fast in-memory vault index access for the agent.

Wraps the BaseApp vault API (vault_query, vault_sections, vault_read_section,
vault_get_properties) so the agent can find and inspect vault notes without
manually Globbing paths + Reading each file.

All ops are read-only and auto-approved in plan mode.

Ops:
  find      — query by tags and/or frontmatter properties → list of notes
  sections  — list ## section names in a note
  section   — read content of one ## section
  props     — read all frontmatter properties for a note
"""

from __future__ import annotations

import json

from emptyos.sdk.agent_tools.base import Tool, ToolResult

MAX_FIND_RESULTS = 50


class VaultQueryTool(Tool):
    name = "VaultQuery"
    description = (
        "Query the vault's in-memory index to find notes by tags/frontmatter, "
        "read section content, or get frontmatter properties — without reading "
        "full files. Much faster than Glob+Read for structured vault data. "
        "Auto-approved (read-only). "
        "ops: find (search by tags/props), sections (list ## headers), "
        "section (read one section body), props (read frontmatter dict)."
    )
    permission = "auto"
    input_schema = {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["find", "sections", "section", "props"],
                "description": (
                    "find — query notes by tags/frontmatter; "
                    "sections — list ## section names for a note; "
                    "section — read body of one ## section; "
                    "props — read all frontmatter properties for a note"
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "op=find: filter by these tags (all must match)",
            },
            "folder": {
                "type": "string",
                "description": "op=find: restrict search to this vault folder prefix (e.g. '10_Projects/')",
            },
            "properties": {
                "type": "object",
                "description": "op=find: filter by these frontmatter key=value pairs",
            },
            "limit": {
                "type": "integer",
                "description": f"op=find: max results (default 20, max {MAX_FIND_RESULTS})",
            },
            "path": {
                "type": "string",
                "description": "op=sections/section/props: vault-relative path to the note (e.g. '10_Projects/foo/foo.md')",
            },
            "section": {
                "type": "string",
                "description": "op=section: section name to read (e.g. 'Timeline')",
            },
        },
        "required": ["op"],
    }

    def is_readonly(self, input: dict) -> bool:
        return True

    def permission_for(self, input: dict) -> str:
        return "auto"

    def permission_summary(self, input: dict) -> str:
        op = input.get("op", "?")
        if op == "find":
            tags = input.get("tags") or []
            props = input.get("properties") or {}
            folder = input.get("folder", "")
            parts = []
            if tags:
                parts.append(f"tags={tags}")
            if props:
                parts.append(f"props={list(props.keys())}")
            if folder:
                parts.append(f"folder={folder!r}")
            return f"VaultQuery find: {', '.join(parts) or 'all'}"
        path = input.get("path", "")
        return f"VaultQuery {op}: {path}"

    async def run(self, app, **kwargs) -> ToolResult:
        op = (kwargs.get("op") or "").strip()

        if op == "find":
            return await self._op_find(app, kwargs)
        elif op == "sections":
            return await self._op_sections(app, kwargs)
        elif op == "section":
            return await self._op_section(app, kwargs)
        elif op == "props":
            return await self._op_props(app, kwargs)
        else:
            return ToolResult(
                ok=False,
                content=f"error: unknown op {op!r}. Use find, sections, section, or props.",
            )

    async def _op_find(self, app, kwargs: dict) -> ToolResult:
        tags = kwargs.get("tags") or None
        folder = kwargs.get("folder") or None
        properties = dict(kwargs.get("properties") or {})
        limit = int(kwargs.get("limit") or 20)
        limit = max(1, min(limit, MAX_FIND_RESULTS))

        results = app.vault_query(tags=tags, folder=folder, **properties)

        if not results:
            desc_parts = []
            if tags:
                desc_parts.append(f"tags={tags}")
            if properties:
                desc_parts.append(f"props={list(properties.keys())}")
            if folder:
                desc_parts.append(f"folder={folder!r}")
            return ToolResult(
                ok=True,
                content=f"No vault notes found matching: {', '.join(desc_parts) or 'query'}",
                display={"results": [], "total": 0},
            )

        total = len(results)
        page = results[:limit]
        lines = [f"Found {total} note(s){f' (showing {limit})' if total > limit else ''}:\n"]
        for r in page:
            path = r.get("path", "")
            note_tags = r.get("tags", [])
            props = r.get("properties", {})
            tag_str = f"  tags: {note_tags}" if note_tags else ""
            prop_strs = [f"  {k}: {v}" for k, v in props.items() if k not in ("tags",)]
            lines.append(f"- {path}")
            if tag_str:
                lines.append(tag_str)
            lines.extend(prop_strs[:5])  # cap noisy frontmatter

        return ToolResult(
            ok=True,
            content="\n".join(lines),
            display={"results": page, "total": total},
        )

    async def _op_sections(self, app, kwargs: dict) -> ToolResult:
        path = (kwargs.get("path") or "").strip()
        if not path:
            return ToolResult(ok=False, content="error: path is required for op=sections")
        sections = app.vault_sections(path)
        if not sections:
            return ToolResult(
                ok=True, content=f"No sections found in: {path}", display={"sections": []}
            )
        lines = [f"Sections in {path}:"]
        for s in sections:
            lines.append(f"  ## {s}")
        return ToolResult(ok=True, content="\n".join(lines), display={"sections": sections})

    async def _op_section(self, app, kwargs: dict) -> ToolResult:
        path = (kwargs.get("path") or "").strip()
        section = (kwargs.get("section") or "").strip()
        if not path:
            return ToolResult(ok=False, content="error: path is required for op=section")
        if not section:
            return ToolResult(ok=False, content="error: section name is required for op=section")
        content = app.vault_read_section(path, section)
        if not content:
            return ToolResult(
                ok=True,
                content=f"Section '## {section}' not found in {path}. "
                f"Use op=sections to list available sections.",
            )
        return ToolResult(
            ok=True,
            content=f"## {section}\n\n{content}",
            display={"path": path, "section": section, "chars": len(content)},
        )

    async def _op_props(self, app, kwargs: dict) -> ToolResult:
        path = (kwargs.get("path") or "").strip()
        if not path:
            return ToolResult(ok=False, content="error: path is required for op=props")
        props = app.vault_get_properties(path)
        if props is None or props == {}:
            return ToolResult(ok=True, content=f"No frontmatter properties found for: {path}")
        lines = [f"Frontmatter for {path}:"]
        for k, v in props.items():
            lines.append(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
        return ToolResult(
            ok=True,
            content="\n".join(lines),
            display={"path": path, "properties": props},
        )
