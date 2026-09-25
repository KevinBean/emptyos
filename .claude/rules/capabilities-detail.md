---
paths:
  - "emptyos/capabilities/**"
  - "emptyos/speechlang.py"
  - "emptyos/cli/commands/send.py"
  - "plugins/edge-tts/**"
  - "plugins/voice-api/**"
  - "plugins/footage/**"
  - "plugins/translate/**"
  - "plugins/email-smtp/**"
  - "emptyos/sdk/i18n.py"
---

# Capabilities — provider notes behind the CLAUDE.md table

Moved out of CLAUDE.md § Architecture (2026-09-25). CLAUDE.md keeps the
capability → provider-chain table; the caveats live here.

- **speak** — edge-tts is **cloud**: an in-process client, but synthesis happens at Microsoft's endpoint, so it declares `trust = "service"`. A Chinese line is routed to edge-tts when the caller pinned nothing — kokoro's Mandarin measured 43% intelligible against edge-tts's 85% through a Whisper round-trip — so intelligible Mandarin here means an off-machine send. `emptyos/speechlang.py` owns the language rule.
- **browse** — playwright (headless Chromium), no human fallback; raises when no provider, the app catches.
- **send** — email-smtp (outbound email, incl. `attachments=[abs paths]`) → human (interactive: deliver by hand). Outbound to third parties → consent gate + leak-scan apply; SMS/Resend/SES plug in here. **The general entry point is `eos send`, a CLI on purpose** — outbound is permanently human-gated, and an HTTP route would be POST-able by any page/agent/`[DO:]` token, so one does not exist. An app may still wire the capability for its own domain (`bookme` does, for booking confirmations).
- **footage** — footage plugin: pexels → pixabay (keyword → downloaded royalty-free clip, content-addressed cache) → human (supply a file). Dark until an API key is set; cloud providers consent-gated. **Built but unconsumed** — no app declares or calls it (verified 2026-08-28); the intended consumers are staged-generation pipelines (MV / podcast slideshow), which still source clips directly.
- **translate** — `translate` plugin: NLLB-200 (local, deterministic, ~200 langs via CTranslate2) → `llm-translate` (think-backed fallback, any language). No human fallback. English is the single authored language; everything else is derived + cached (`data/i18n/`). Powers the UI-chrome layer (`eos-i18n.js`) + `BaseApp.translate()`. See `emptyos/sdk/i18n.py`.
- **pronounce** — no human fallback: a human can't score per-phone accuracy by ear.
- **model** — domains `articulated` (joints) / `static_mesh` (single rigid body); cloud providers (Articraft API, MeshyAI) plug in here.
- **artifact** — shapes: `3d-scene` (Three.js), `immersive-scene` (modern Three.js ESM — PBR + bloom + GLSL), `svg-diagram`, `schematic`, `network-graph` (vis-network), `chart` (Chart.js), `anim-explainer`, `slide-deck` (reveal.js), `math-explainer` (KaTeX), `mermaid`, `game-2d` (Kaboom.js). Cloud "render via claude.ai artifacts" backends plug in here.
