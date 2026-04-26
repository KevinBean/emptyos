"""Templates — create notes from templates with placeholder substitution."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


class TemplateApp(BaseApp):

    def _template_dir(self) -> Path:
        return self.manifest.path / "templates"

    async def list_templates(self) -> list[str]:
        """List available template names."""
        d = self._template_dir()
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.md"))

    async def use(self, template: str, output_path: str = "", **variables) -> str:
        """Create a note from a template. Returns the output path."""
        tmpl_file = self._template_dir() / f"{template}.md"
        if not tmpl_file.exists():
            raise FileNotFoundError(f"Template not found: {template}")

        content = tmpl_file.read_text()

        # Built-in variables
        variables.setdefault("date", str(date.today()))
        variables.setdefault("title", template.replace("-", " ").title())

        # Replace {{var}} placeholders
        for key, value in variables.items():
            content = content.replace("{{" + key + "}}", str(value))

        # Default output path
        if not output_path:
            notes = self.kernel.config.get("notes.path", "")
            base = notes or str(self.kernel.config.data_dir)
            title_slug = variables.get("title", template).replace(" ", "-")
            output_path = f"{base}/{title_slug}.md"

        await self.write(output_path, content)
        await self.emit("tmpl:used", {"template": template, "output": output_path})
        return output_path

    @cli_command("tmpl", help="Create notes from templates")
    async def cmd_tmpl(self, action: str = "list", name: str = "", title: str = "", output: str = ""):
        if action == "list":
            templates = await self.list_templates()
            if not templates:
                self.print_rich("[dim]No templates found.[/dim]")
                return
            for t in templates:
                self.print_rich(f"  {t}")
        elif action == "use" and name:
            kwargs = {}
            if title:
                kwargs["title"] = title
            path = await self.use(name, output, **kwargs)
            self.print_rich(f"[green]Created:[/green] {path}")
        else:
            self.print_rich("[dim]Usage: eos tmpl {list|use} [name] [--title T] [--output PATH][/dim]")

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        return await self.list_templates()

    @web_route("POST", "/api/use")
    async def api_use(self, request):
        data = await request.json()
        template = data.get("template", "").strip()
        if not template:
            return {"error": "template is required"}
        variables = {k: v for k, v in data.items() if k not in ("template", "output")}
        try:
            path = await self.use(template, data.get("output", ""), **variables)
            return {"ok": True, "path": path}
        except FileNotFoundError as e:
            return {"error": str(e)}

    @web_route("GET", "/api/preview/{name}")
    async def api_preview(self, request):
        name = request.path_params["name"]
        tmpl_file = self._template_dir() / f"{name}.md"
        if not tmpl_file.exists():
            return {"error": "template not found"}
        return {"name": name, "content": tmpl_file.read_text(encoding="utf-8")}

    @web_route("POST", "/api/generate")
    async def api_generate(self, request):
        """AI generates a new template from a description."""
        data = await request.json()
        desc = data.get("description", "")
        name = data.get("name", "")
        if not desc or not name:
            return {"error": "description and name required"}
        result = await self.think(
            f"Generate a markdown note template for: {desc}\n\n"
            f"Use {{{{variable}}}} placeholders for dynamic fields like {{{{title}}}}, {{{{date}}}}.\n"
            f"Include YAML frontmatter. Return ONLY the template content.",
            domain="text", temperature=0.6,
        )
        # Save template
        tmpl_dir = self._template_dir()
        tmpl_dir.mkdir(parents=True, exist_ok=True)
        slug = name.lower().replace(" ", "-")
        path = tmpl_dir / f"{slug}.md"
        path.write_text(result.strip(), encoding="utf-8")
        return {"name": slug, "saved": True, "content": result.strip()}
