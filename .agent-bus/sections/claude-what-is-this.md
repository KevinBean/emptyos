

EmptyOS is an AI-powered operating system — a **mind companion** that thinks and creates alongside the user. **An OS is just a human doing things** — reading, writing, thinking, searching. Tools are optional accelerators. A markdown vault serves as the **hard drive** — mounted externally, swappable, human-readable. Kernel state lives in SQLite/JSON.

**North star: the human owns judgment; the system owns reversible execution.** "With you, not for you" is a promise about *judgment* — the user owns the direction, the taste, and every irreversible or outbound action — not a tax on *execution*. So the default splits by reversibility, not by whether the action changes state:

- **Reversible / internal actions auto-run** (task/capture/journal/kb-tag/reminders/frontmatter, and any verb with a recorded inverse). The safety net is **audit + one-click undo after**, not approval before.
- **Irreversible / external / billing actions stay human-gated** — `publish.deploy`, outbound email/message/social to third parties, cloud calls that spend money, bulk-destructive ops without a verified undo. These are never auto-eligible regardless of any grant.

The line between the two is the verb-registry eligibility class (`stable` → auto, `gated`/`never` → gate). A **grant** lets a *gated* verb auto-run in a scope; a **hold** pauses auto for a *stable* verb. Both are per-actor + per-verb + per-scope, never a global mode; the system never widens that policy on its own, and its audit surface must be one the user actually reads. See `.claude/rules/autopilot-grants.md` and `.claude/rules/verb-registry.md`.

Three runtime modes: **daemon** (port 9000, apps + events + agents), **CLI** (one-shot commands), **conversation** (AI coding tool loads codebase as context, evolves the system). Conversation mode is the primary growth mechanism. See `docs/DESIGN.md` for architecture, philosophy, and the consciousness model. Non-Claude-Code AI tools: see `AGENTS.md`.
