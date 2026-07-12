

EmptyOS is an AI-powered operating system — a **mind companion** that thinks and creates alongside the user. **An OS is just a human doing things** — reading, writing, thinking, searching. Tools are optional accelerators. A markdown vault serves as the **hard drive** — mounted externally, swappable, human-readable. Kernel state lives in SQLite/JSON.

**North star: the human owns judgment; the system owns reversible execution.** "With you, not for you" is a promise about *judgment* — the user owns the direction, the taste, and every irreversible or outbound action — not a tax on *execution*. So the default splits by reversibility, not by whether the action changes state:

- **Reversible / internal actions auto-run** (task/capture/journal/kb-tag/reminders/frontmatter, and any verb with a recorded inverse). The safety net is **audit + one-click undo after**, not approval before. Re-asking the human to bless each predictable, undoable action is friction that strangles automation, not safety — the old "review every state change" default left the auto-apply path as dead code.
- **Irreversible / external / billing actions stay human-gated** — `publish.deploy`, outbound email/message/social to third parties, cloud calls that spend money, bulk-destructive ops without a verified undo. Here "for you" really would be wrong: there's nothing to undo. These are never auto-eligible regardless of any grant.

The line between the two is the verb-registry eligibility class (`stable` → auto, `gated`/`never` → gate; see `.claude/rules/verb-registry.md`). A **grant** now means "let this *gated* verb auto-run in this scope"; a **hold** is its opposite ("pause auto for this *stable* verb right now"). Both are per-actor + per-verb + per-scope, never a global mode. The system executes freely *within* a policy the human set once — it never widens that policy on its own, and the audit surface must be one the user actually reads. See `.claude/rules/autopilot-grants.md`.

Three runtime modes: **daemon** (port 9000, apps + events + agents), **CLI** (one-shot commands), **conversation** (AI coding tool loads codebase as context, evolves the system). Conversation mode is the primary growth mechanism. See `docs/DESIGN.md` for architecture, philosophy, and the consciousness model.

Non-Claude-Code AI tools: see `AGENTS.md`.
