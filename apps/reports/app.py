"""Reports — professional documents with rigid templates, structured tables, PDF + DOCX export.

Sibling to `apps/publish/` (sites) and `apps/personal/fiction-engine/` (long-form fiction).
This app owns formal deliverables: PDR, CDR, TRR, proposals, specs, reports.

Storage: `{vault}/30_Resources/EmptyOS/reports/{doc-id}/` with:
    _meta.md        — frontmatter: title, type, version, authors, approvers, project_id, status
    _outline.md     — YAML list of sections (order + render directives)
    sections/*.md   — per-section markdown
    tables/*.yaml   — structured data (requirements, risks, verification, stakeholders)
    figures/*       — images referenced via `![[name.png|caption]]`
    exports/        — built PDF/DOCX
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import parse_frontmatter, slugify, strip_frontmatter

from . import tables as tables_mod
from .render_html import assemble_html
from .templates import TEMPLATES, TABLE_SCHEMAS, get_template, list_templates, table_schema


# --- LLM prompts (named constants per CLAUDE.md rule 12) ---

WRITER_SYSTEM = """You are a technical writing assistant inside a professional-documents editor.
The document is formal — an engineering design review, technical proposal, or specification.
Your voice is precise, neutral, and active. You do NOT:
  - add filler, hedging, or corporate platitudes ("world-class", "innovative", "robust")
  - invent numbers, dates, names, or citations
  - pad short answers to look more substantial
  - add sections or headings the user did not request
  - remove frontmatter, tables, or figure references
Return only the rewritten section markdown. No preamble, no explanation.
"""

POLISH_PROMPT = (
    "Tighten this section. Fix grammar, remove redundancy, make verbs active. "
    "Keep the same facts and structure. Keep all [[wiki-links]], {{table:...}} tokens, "
    "and ![[figure.png|caption]] references intact.\n\nSection:\n\n"
)

EXPAND_PROMPT = (
    "Expand this section with additional necessary detail that a reviewer would expect in a "
    "formal design document. Add only what's missing — do NOT restate what is already said. "
    "Keep all tokens and links intact.\n\nSection:\n\n"
)

COMPRESS_PROMPT = (
    "Compress this section to roughly half its length while preserving all facts, numbers, and "
    "references. Keep all tokens and links intact. Prefer shorter sentences and active voice.\n\n"
    "Section:\n\n"
)

SCAFFOLD_PROMPT_TEMPLATE = """Draft a first-pass body for a section of a formal {doc_type_human} document.

Section: {section_title}
Purpose: {prompt_hint}

Context (from the document's meta):
{meta_block}

User hints:
{user_hints}

Rules:
- Write about 150-300 words. If the section genuinely needs more, go longer, but avoid padding.
- Use formal neutral voice. No marketing language. No hedging.
- Where structured data belongs (requirements, risks), insert the token {{{{table:NAME}}}} on its own line.
- Where a figure belongs, insert ![[figure-filename.png|caption]] on its own line — use a plausible filename the author will replace.
- Do NOT restate the section title — the editor already shows it.
"""


_META_DEFAULTS = {
    "title": "",
    "subtitle": "",
    "type": "report",
    "version": "0.1",
    "date": "",
    "authors": [],
    "organisation": "",
    "project_id": "",
    "status": "draft",
    "approvers": [],
    "tags": ["report"],
}


# Outline render directives we recognise
_OUTLINE_DIRECTIVES = {"", "signoff"}  # plus "table:<name>"


class ReportsApp(BaseApp):
    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def _reports_root(self) -> Path:
        """Directory that holds all report folders — one per doc."""
        # Uses BaseApp.vault_dir which defaults to {vault}/30_Resources/EmptyOS/reports/
        return self.vault_dir

    def _report_dir(self, doc_id: str) -> Path:
        return self._reports_root / doc_id

    def _rel(self, doc_id: str, *parts: str) -> str:
        """Vault-root-relative, forward-slash path for vault-index calls."""
        p = self._report_dir(doc_id).joinpath(*parts)
        return str(p.relative_to(self.vault_root)).replace("\\", "/")

    @property
    def _stylesheet_path(self) -> Path:
        return Path(__file__).parent / "static" / "report.css"

    def _load_stylesheet(self) -> str:
        custom = (self.setting("reports.pdf_stylesheet") or "").strip()
        if custom:
            custom_p = Path(custom)
            if custom_p.exists():
                try:
                    return custom_p.read_text(encoding="utf-8")
                except Exception:
                    pass
        try:
            return self._stylesheet_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # ------------------------------------------------------------------
    # CLI
    # ------------------------------------------------------------------

    @cli_command("report", help="Manage professional documents — PDR, CDR, TRR, proposals, specs")
    async def cmd_report(self, action: str = "list", **kwargs):
        if action == "list":
            reports = self._scan_reports()
            if not reports:
                self.print_rich("[dim]No reports yet. Use the web UI at /reports/ to create one.[/dim]")
                return
            self.print_rich(f"[bold]Reports ({len(reports)}):[/bold]")
            for r in reports:
                self.print_rich(
                    f"  [bold]{r['title']}[/bold]  "
                    f"[dim]{r['type']} v{r['version']} — {r['status']}[/dim]"
                )
        elif action == "templates":
            self.print_rich("[bold]Available templates:[/bold]")
            for t in list_templates():
                self.print_rich(
                    f"  [bold]{t['id']}[/bold]  {t['name']}  "
                    f"[dim]({t['section_count']} sections, {t['table_count']} tables)[/dim]"
                )
        else:
            self.print_rich("[dim]Usage: eos report {list|templates}[/dim]")

    # ------------------------------------------------------------------
    # Scanning / loading
    # ------------------------------------------------------------------

    def _scan_reports(self) -> list[dict]:
        root = self._reports_root
        if not root.exists():
            return []
        out: list[dict] = []
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            meta_path = child / "_meta.md"
            if not meta_path.exists():
                continue
            try:
                meta = parse_frontmatter(meta_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            out.append({
                "id": child.name,
                "title": meta.get("title") or child.name,
                "type": meta.get("type") or "report",
                "version": str(meta.get("version") or "0.1"),
                "status": meta.get("status") or "draft",
                "project_id": meta.get("project_id") or "",
                "updated": _mtime(child),
            })
        return out

    def _load_outline(self, doc_id: str) -> list[dict]:
        outline_path = self._report_dir(doc_id) / "_outline.md"
        if not outline_path.exists():
            return []
        try:
            import yaml
        except ImportError:
            return []
        text = outline_path.read_text(encoding="utf-8")
        body = strip_frontmatter(text)
        body = re.sub(r"^#\s+Outline\s*\n", "", body, count=1)
        try:
            raw = yaml.safe_load(body)
        except Exception:
            return []
        return [r for r in raw if isinstance(r, dict) and "slug" in r] if isinstance(raw, list) else []

    def _save_outline(self, doc_id: str, outline: list[dict]) -> None:
        try:
            import yaml
        except ImportError:
            raise RuntimeError("pyyaml required. pip install pyyaml")
        outline_path = self._report_dir(doc_id) / "_outline.md"
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        body = "# Outline\n\n" + yaml.safe_dump(outline, sort_keys=False, allow_unicode=True)
        outline_path.write_text(body, encoding="utf-8")

    def _load_meta(self, doc_id: str) -> dict:
        meta_path = self._report_dir(doc_id) / "_meta.md"
        if not meta_path.exists():
            return {}
        raw = meta_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(raw) or {}
        fm["_body"] = strip_frontmatter(raw)
        return fm

    def _save_meta(self, doc_id: str, meta: dict) -> None:
        """Persist meta as frontmatter + free-text body at _meta.md, indexed via VaultIndex."""
        body = meta.pop("_body", "") if "_body" in meta else ""
        fm = {k: v for k, v in meta.items() if not k.startswith("_")}
        self._report_dir(doc_id).mkdir(parents=True, exist_ok=True)
        # vault_create_note overwrites + indexes. Suitable for both create and full rewrite.
        self.vault_create_note(self._rel(doc_id, "_meta.md"), fm, body or "")

    # ------------------------------------------------------------------
    # API — templates
    # ------------------------------------------------------------------

    @web_route("GET", "/api/templates")
    async def api_templates(self, request):
        return {"templates": list_templates(), "table_schemas": TABLE_SCHEMAS}

    # ------------------------------------------------------------------
    # API — reports CRUD
    # ------------------------------------------------------------------

    @web_route("GET", "/api/reports")
    async def api_list_reports(self, request):
        return {"reports": self._scan_reports()}

    @web_route("POST", "/api/reports")
    async def api_create_report(self, request):
        data = await request.json()
        template_id = (data.get("template") or self.setting("reports.default_template", "report") or "report").strip()
        title = (data.get("title") or "").strip() or "Untitled Report"
        project_id = (data.get("project_id") or "").strip()

        tpl = get_template(template_id)
        if not tpl:
            return {"error": f"Unknown template: {template_id}"}

        # Unique id based on title
        base = slugify(title) or "report"
        doc_id = base
        suffix = 2
        while self._report_dir(doc_id).exists():
            doc_id = f"{base}-{suffix}"
            suffix += 1

        report_dir = self._report_dir(doc_id)
        (report_dir / "sections").mkdir(parents=True, exist_ok=True)
        (report_dir / "tables").mkdir(parents=True, exist_ok=True)
        (report_dir / "figures").mkdir(parents=True, exist_ok=True)
        (report_dir / "exports").mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        meta = dict(_META_DEFAULTS)
        meta.update({
            "title": title,
            "type": template_id,
            "date": today,
            "authors": [self.setting("reports.author_name") or ""] if self.setting("reports.author_name") else [],
            "organisation": self.setting("reports.organisation") or "",
            "project_id": project_id,
            "approvers": list(tpl.get("approvers") or []),
            "tags": ["report", f"report-{template_id}"],
        })
        self._save_meta(doc_id, meta)

        # Outline
        outline = []
        for s in tpl["sections"]:
            entry = {
                "slug": s["slug"],
                "title": s["title"],
                "status": "draft",
            }
            if s.get("render"):
                entry["render"] = s["render"]
            if s.get("required"):
                entry["required"] = True
            outline.append(entry)
        self._save_outline(doc_id, outline)

        # Sections — written via vault_create_note so they land in the VaultIndex.
        for s in tpl["sections"]:
            slug = s["slug"]
            body = ""
            hint = s.get("prompt_hint") or ""
            if hint:
                body += f"<!-- prompt: {hint} -->\n\n"
            if s.get("render", "").startswith("table:"):
                table_name = s["render"].split(":", 1)[1]
                body += f"{{{{table:{table_name}}}}}\n"
            self.vault_create_note(
                self._rel(doc_id, "sections", f"{slug}.md"),
                {"status": "draft", "title": s["title"], "parent_report": doc_id,
                 "tags": ["report-section", f"report-{template_id}-section"]},
                body,
            )

        # Scaffold tables
        for table_name in tpl.get("tables", []):
            tables_mod.save_table(
                report_dir / "tables" / f"{table_name}.yaml",
                tables_mod.scaffold_rows(table_name, count=2),
            )

        await self.emit("reports:created", {"id": doc_id, "type": template_id, "title": title})
        return {"ok": True, "id": doc_id, "title": title, "type": template_id}

    @web_route("GET", "/api/reports/{doc_id}")
    async def api_get_report(self, request):
        doc_id = request.path_params["doc_id"]
        if not self._report_dir(doc_id).exists():
            return _not_found(doc_id)
        meta = self._load_meta(doc_id)
        outline = self._load_outline(doc_id)
        tables_present = {}
        tables_dir = self._report_dir(doc_id) / "tables"
        if tables_dir.exists():
            for yf in tables_dir.glob("*.yaml"):
                tables_present[yf.stem] = len(tables_mod.load_table(yf))
        figures: list[str] = []
        figures_dir = self._report_dir(doc_id) / "figures"
        if figures_dir.exists():
            figures = [p.name for p in sorted(figures_dir.iterdir()) if p.is_file()]
        exports = []
        exports_dir = self._report_dir(doc_id) / "exports"
        if exports_dir.exists():
            for f in sorted(exports_dir.iterdir(), reverse=True):
                if f.is_file():
                    exports.append({"name": f.name, "size": f.stat().st_size, "mtime": _mtime(f)})
        meta.pop("_body", None)
        return {
            "id": doc_id,
            "meta": meta,
            "outline": outline,
            "tables_present": tables_present,
            "table_schemas": {name: TABLE_SCHEMAS[name] for name in tables_present if name in TABLE_SCHEMAS},
            "figures": figures,
            "exports": exports,
        }

    @web_route("PATCH", "/api/reports/{doc_id}/meta")
    async def api_update_meta(self, request):
        doc_id = request.path_params["doc_id"]
        if not self._report_dir(doc_id).exists():
            return _not_found(doc_id)
        data = await request.json()
        updates = {k: v for k, v in (data.get("meta") or {}).items() if not k.startswith("_")}
        # vault_update rewrites frontmatter in place and re-indexes; preserves body.
        self.vault_update(self._rel(doc_id, "_meta.md"), updates)
        await self.emit("reports:section-updated", {"id": doc_id, "field": "meta"})
        return {"ok": True}

    @web_route("DELETE", "/api/reports/{doc_id}")
    async def api_delete_report(self, request):
        doc_id = request.path_params["doc_id"]
        rpt_dir = self._report_dir(doc_id)
        if not rpt_dir.exists():
            return _not_found(doc_id)
        # Move to a `_trash/` subfolder instead of permanent delete — vault safety rule.
        trash = self._reports_root / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        target = trash / f"{doc_id}-{stamp}"
        suffix = 2
        while target.exists():
            target = trash / f"{doc_id}-{stamp}-{suffix}"
            suffix += 1
        rpt_dir.rename(target)
        return {"ok": True, "moved_to": str(target.relative_to(self._reports_root))}

    # ------------------------------------------------------------------
    # API — sections
    # ------------------------------------------------------------------

    @web_route("GET", "/api/reports/{doc_id}/sections/{slug}")
    async def api_get_section(self, request):
        doc_id = request.path_params["doc_id"]
        slug = request.path_params["slug"]
        section_path = self._report_dir(doc_id) / "sections" / f"{slug}.md"
        if not section_path.exists():
            return {"error": f"Section not found: {slug}"}
        raw = section_path.read_text(encoding="utf-8")
        fm = parse_frontmatter(raw) or {}
        body = strip_frontmatter(raw)
        return {"slug": slug, "meta": fm, "body": body}

    @web_route("PUT", "/api/reports/{doc_id}/sections/{slug}")
    async def api_save_section(self, request):
        doc_id = request.path_params["doc_id"]
        slug = request.path_params["slug"]
        if not self._report_dir(doc_id).exists():
            return _not_found(doc_id)
        data = await request.json()
        body = data.get("body", "")
        fm_updates = data.get("meta") or {}

        section_path = self._report_dir(doc_id) / "sections" / f"{slug}.md"
        existing_fm = {}
        if section_path.exists():
            existing_fm = parse_frontmatter(section_path.read_text(encoding="utf-8")) or {}
        existing_fm.update(fm_updates)

        # vault_create_note overwrites the full file + re-indexes.
        self.vault_create_note(self._rel(doc_id, "sections", f"{slug}.md"), existing_fm, body or "")

        # If status was updated, mirror it to the outline for the list view
        if "status" in fm_updates:
            outline = self._load_outline(doc_id)
            for o in outline:
                if o.get("slug") == slug:
                    o["status"] = fm_updates["status"]
            self._save_outline(doc_id, outline)

        await self.emit("reports:section-updated", {"id": doc_id, "slug": slug})
        return {"ok": True}

    # ------------------------------------------------------------------
    # API — tables
    # ------------------------------------------------------------------

    @web_route("GET", "/api/reports/{doc_id}/tables/{name}")
    async def api_get_table(self, request):
        doc_id = request.path_params["doc_id"]
        name = request.path_params["name"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        rows = tables_mod.load_table(rpt / "tables" / f"{name}.yaml")
        schema = table_schema(name) or {"columns": [], "id_prefix": ""}
        return {"name": name, "rows": rows, "schema": schema, "next_id": tables_mod.next_id(rows, schema.get("id_prefix", ""))}

    @web_route("PUT", "/api/reports/{doc_id}/tables/{name}")
    async def api_save_table(self, request):
        doc_id = request.path_params["doc_id"]
        name = request.path_params["name"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        data = await request.json()
        rows = data.get("rows") or []
        if not isinstance(rows, list):
            return {"error": "rows must be a list"}
        tables_mod.save_table(rpt / "tables" / f"{name}.yaml", [r for r in rows if isinstance(r, dict)])
        await self.emit("reports:section-updated", {"id": doc_id, "table": name})
        return {"ok": True, "count": len(rows)}

    # ------------------------------------------------------------------
    # API — figures (upload)
    # ------------------------------------------------------------------

    @web_route("POST", "/api/reports/{doc_id}/figures")
    async def api_upload_figure(self, request):
        doc_id = request.path_params["doc_id"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            return {"error": "No file provided"}
        filename = getattr(upload, "filename", "") or "figure.png"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", filename)
        target = rpt / "figures" / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        data = await upload.read()
        target.write_bytes(data)
        return {"ok": True, "name": safe}

    # ------------------------------------------------------------------
    # API — LLM writer actions
    # ------------------------------------------------------------------

    @web_route("POST", "/api/reports/{doc_id}/ai")
    async def api_ai_action(self, request):
        doc_id = request.path_params["doc_id"]
        if not self._report_dir(doc_id).exists():
            return _not_found(doc_id)
        data = await request.json()
        action = data.get("action", "")
        text = (data.get("text") or "").strip()

        if action in ("polish", "expand", "compress"):
            if not text:
                return {"error": "No text provided"}
            prompt_map = {
                "polish": POLISH_PROMPT,
                "expand": EXPAND_PROMPT,
                "compress": COMPRESS_PROMPT,
            }
            result = await self.think(
                prompt_map[action] + text,
                system=WRITER_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            return {"text": result, "action": action, "provenance": self.last_provenance()}

        if action == "scaffold":
            # Draft a first-pass body for an empty section using its template hint.
            slug = (data.get("slug") or "").strip()
            user_hints = (data.get("hints") or "").strip()
            if not slug:
                return {"error": "slug is required for scaffold"}
            meta = self._load_meta(doc_id)
            tpl = get_template(meta.get("type") or "report") or {}
            section = next((s for s in tpl.get("sections", []) if s["slug"] == slug), None)
            if not section:
                return {"error": f"Section not found in template: {slug}"}
            meta_block = (
                f"- Title: {meta.get('title', '')}\n"
                f"- Type: {meta.get('type', '')}\n"
                f"- Project: {meta.get('project_id', '')}\n"
                f"- Organisation: {meta.get('organisation', '')}"
            )
            prompt = SCAFFOLD_PROMPT_TEMPLATE.format(
                doc_type_human=tpl.get("name", "report"),
                section_title=section["title"],
                prompt_hint=section.get("prompt_hint", ""),
                meta_block=meta_block,
                user_hints=user_hints or "(none)",
            )
            result = await self.think(prompt, system=WRITER_SYSTEM, domain="text", temperature=0.5)
            return {"text": result, "action": "scaffold", "provenance": self.last_provenance()}

        return {"error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------
    # API — preview + exports
    # ------------------------------------------------------------------

    @web_route("GET", "/api/reports/{doc_id}/preview")
    async def api_preview(self, request):
        from starlette.responses import HTMLResponse
        doc_id = request.path_params["doc_id"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        css = self._load_stylesheet()
        html = assemble_html(
            rpt,
            stylesheet_inline=css,
            assets_as_file_urls=False,
            figure_url_prefix=f"/reports/api/reports/{doc_id}/figures/",
        )
        return HTMLResponse(html)

    @web_route("GET", "/api/reports/{doc_id}/figures/{name}")
    async def api_get_figure(self, request):
        """Serve an uploaded figure file (PNG/JPG/SVG/GIF/WebP) from the report's figures/ dir."""
        from starlette.responses import FileResponse, Response
        doc_id = request.path_params["doc_id"]
        name = request.path_params["name"]
        if "/" in name or "\\" in name or ".." in name:
            return Response(status_code=400, content="Invalid filename")
        p = self._report_dir(doc_id) / "figures" / name
        if not p.exists() or not p.is_file():
            return Response(status_code=404, content="Not found")
        ext = p.suffix.lower()
        media = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
        return FileResponse(str(p), media_type=media)

    @web_route("POST", "/api/reports/{doc_id}/export/pdf")
    async def api_export_pdf(self, request):
        doc_id = request.path_params["doc_id"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        from .render_pdf import PlaywrightMissing, to_pdf
        css = self._load_stylesheet()
        html = assemble_html(rpt, stylesheet_inline=css, assets_as_file_urls=True)
        meta = self._load_meta(doc_id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"{doc_id}-v{meta.get('version', '0.1')}-{stamp}.pdf"
        out = rpt / "exports" / fname
        try:
            await to_pdf(html, out)
        except PlaywrightMissing as e:
            return {"error": str(e), "missing_dep": "playwright"}
        except Exception as e:
            self.log_error(f"PDF export failed: {e}", data={"doc_id": doc_id})
            return {"error": f"PDF export failed: {e}"}
        await self.emit("reports:exported", {"id": doc_id, "format": "pdf", "file": str(out)})
        return {"ok": True, "file": fname, "size": out.stat().st_size}

    @web_route("POST", "/api/reports/{doc_id}/export/docx")
    async def api_export_docx(self, request):
        doc_id = request.path_params["doc_id"]
        rpt = self._report_dir(doc_id)
        if not rpt.exists():
            return _not_found(doc_id)
        from .render_docx import PythonDocxMissing, to_docx
        meta = self._load_meta(doc_id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"{doc_id}-v{meta.get('version', '0.1')}-{stamp}.docx"
        out = rpt / "exports" / fname
        try:
            to_docx(rpt, out)
        except PythonDocxMissing as e:
            return {"error": str(e), "missing_dep": "python-docx"}
        except Exception as e:
            self.log_error(f"DOCX export failed: {e}", data={"doc_id": doc_id})
            return {"error": f"DOCX export failed: {e}"}
        await self.emit("reports:exported", {"id": doc_id, "format": "docx", "file": str(out)})
        return {"ok": True, "file": fname, "size": out.stat().st_size}

    @web_route("GET", "/api/reports/{doc_id}/export/file/{name}")
    async def api_download_export(self, request):
        from starlette.responses import FileResponse, Response
        doc_id = request.path_params["doc_id"]
        name = request.path_params["name"]
        # Path traversal guard
        if "/" in name or "\\" in name or ".." in name:
            return Response(status_code=400, content="Invalid filename")
        p = self._report_dir(doc_id) / "exports" / name
        if not p.exists():
            return Response(status_code=404, content="Not found")
        media = "application/pdf" if p.suffix.lower() == ".pdf" else "application/octet-stream"
        if p.suffix.lower() == ".docx":
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(str(p), media_type=media, filename=name)

    # ------------------------------------------------------------------
    # API — cross-app: projects integration
    # ------------------------------------------------------------------

    @web_route("GET", "/api/for-project/{project_id}")
    async def api_for_project(self, request):
        project_id = request.path_params["project_id"]
        out = [r for r in self._scan_reports() if r.get("project_id") == project_id]
        return {"project_id": project_id, "reports": out}

    # ------------------------------------------------------------------
    # App-to-app RPC (self.call_app target)
    # ------------------------------------------------------------------

    async def list_for_project(self, project_id: str) -> list[dict]:
        return [r for r in self._scan_reports() if r.get("project_id") == project_id]


# --- module-level helpers ---------------------------------------------------


def _mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def _not_found(doc_id: str) -> dict:
    return {"error": f"Report not found: {doc_id}"}
