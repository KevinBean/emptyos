

Adapt the canonical sources to your own tool's format:

- **Cursor** — `.cursorrules` at root (condensed) or `.cursor/rules/*.md` (full). Copy the architecture + development rules from `CLAUDE.md`
- **Windsurf** — `.windsurfrules` at root. Similar to Cursor
- **GitHub Copilot** — `.github/copilot-instructions.md`. Auto-loaded
- **Aider** — `CONVENTIONS.md` at root, or pass `CLAUDE.md` via `--read`
- **Other** — consult your tool's docs for project-context conventions

Generated tool-specific files are yours to manage. Add them to `.gitignore` unless the team has agreed to commit them. The canonical sources are the ones listed above.
