

5 backends × 8 frontends. The mental map of every LLM-talking surface in EmptyOS:

| Backend | Web (dedicated) | Page-sidebar | CLI |
|---|---|---|---|
| **agent** — autonomous tool-loop, per-tool consent | `/agent/` (chat shape), `/code/` (code-IDE shape — tree + preview + diff + terminal + chat) | (Phase 3.5 future) | `eos chat` (alias: `eos code`) |
| **rooms** — multi-participant chat, `[DO:]` review gate | `/rooms/` | ✓ primary | `eos rooms` (`--code` preset) |
| **assistant** — multi-provider research, vault-aware Q&A | `/assistant/` | ✓ fallback | — |
| **voice-assistant (Aura)** — voice intents, companions, narration | `/voice-assistant/` + phone PWA | — | — |
| **staff** — scheduled + on-demand agents, HITL approvals | `/staff/` | — | `eos staff` (operational) |

Four families to choose between: **TOOL LOOP** (agent), **CONVERSATION** (rooms + assistant), **VOICE** (voice-assistant), **CRON** (staff). Don't merge across families — they were audited 2026-05-16 and each has unique value. Full layout, decision tree, coexist-not-merge decisions, and cross-references: `docs/CONVERSATION-STACK.md`.
