# AI Agents — Setup Guide

This file is for AI coding agents that are **not Claude Code**. Claude Code reads `CLAUDE.md` automatically and needs no setup.

If you are Cursor, Windsurf, GitHub Copilot, Aider, Cody, Continue, or any other AI coding tool — this file tells you where EmptyOS keeps its context so you can self-configure.

## The Canonical Sources

| What | Where | Purpose |
|---|---|---|
| **System prompt** | `CLAUDE.md` | Full architecture, capabilities, conventions, development rules, gotchas |
| **Architecture doc** | `docs/DESIGN.md` | Deep design: runtime modes, capability system, consciousness model, UI philosophy |
| **Behavioral rules** | `.claude/rules/*.md` | Rules (docs-sync, vault-operator) |
| **Skills / procedures** | `.claude/skills/*/SKILL.md` | Reusable procedures |
| **Public docs** | `README.md`, `docs/GETTING-STARTED.md`, `docs/APP-DEVELOPMENT.md` | External-facing |

Read `CLAUDE.md` first — it is the boot prompt. It loads the architecture, philosophy, and dev rules needed for coherent contributions.

## Self-Configuration

Adapt the canonical sources to your own tool's format:

- **Cursor** — `.cursorrules` at root (condensed) or `.cursor/rules/*.md` (full). Copy the architecture + development rules from `CLAUDE.md`
- **Windsurf** — `.windsurfrules` at root. Similar to Cursor
- **GitHub Copilot** — `.github/copilot-instructions.md`. Auto-loaded
- **Aider** — `CONVENTIONS.md` at root, or pass `CLAUDE.md` via `--read`
- **Other** — consult your tool's docs for project-context conventions

Generated tool-specific files are yours to manage. Add them to `.gitignore` unless the team has agreed to commit them. The canonical sources are the ones listed above.

## Runtime vs Conversation

EmptyOS's runtime does not depend on any AI coding tool — it uses the `think` capability with swappable LLM providers (see `docs/DESIGN.md` "Capability System"). The AI coding tool only participates in **conversation mode**, which is where the system evolves (see `docs/DESIGN.md` "Three Runtime Modes").
