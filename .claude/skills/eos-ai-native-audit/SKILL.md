---
name: eos-ai-native-audit
description: Audit EmptyOS apps for AI-nativeness — backend LLM use vs assistant reach (verbs/voice/slash/field-suggest) vs visible AI UI (modelPill/provenance/aiFormFill/✨) — and rank the gaps. Use when the user says "AI-native audit", "check AI integration", "which apps integrate with the assistant", "AI-native score", or after adding a new platform AI mechanism to find non-adopters. NOT for auditing the visual design of AI surfaces (use eos-design-system-audit DL-7), whole-system architecture health (use eos-architecture-review), or model quality benchmarking (use eos-model-bench-scenario-audit).
---

# EmptyOS AI-Native Audit

Measures how AI-native each app is along three orthogonal axes, then applies judgment to rank the gaps. The deterministic substrate is `scripts/check_ai_native.py`; this skill is the triage + narrative layer on top (same split as `check_ui_structure.py` ↔ `eos-design-system-audit`).

| Axis | Question | Detected via |
|---|---|---|
| **Backend AI** | Does the app call the LLM? | `self.think/think_stream/think_cached/select/suggest_field` in app Python |
| **Assistant reach** | Can an assistant (voice/slash/MCP/agent) *act on* this app? | `[[provides.verbs]]`, voice intents, `[provides.assistant]`, `[[provides.field_suggest]]`, `[provides.prompts]` |
| **AI UI on the page** | Can the user *see and steer* the AI? | `EOS_UI.modelPill/provenance/aiFormFill`, `data-suggest-field`, `registerActions` |

Classification: **exemplar** (backend + reach + chip) · **partial** · **dark** (backend think, no chip, no reach — the finding class) · **surface** (the conversation stack itself, exempt) · **no-ai** (deterministic calculators/connectors — correct by design, never a finding).

Reference audit + full pattern catalog: `{vault}/30_Resources/EmptyOS/insights/outputs/2026-07-10-ai-native-audit.md`.

## When to run

- User asks "AI-native audit", "check AI integration", "AI-native score", "which apps talk to the assistant"
- A new platform AI mechanism landed (a new `EOS_UI` AI helper, a new verb surface) — find non-adopters
- Periodically (quarterly-ish), to watch the dark-AI count trend against the last report

## When NOT to run

- Judging one AI surface's *design* (provenance chip styling, streaming pulse) → `eos-design-system-audit` DL-7
- System wiring/topology health → `eos-architecture-review`
- "Which model is best for X" → `eos-model-bench-scenario-audit`
- Don't recommend AI onto correctly-no-AI apps (engineering calculators, connectors, view layers). A cable-thrust calculator gaining a chat box is a regression, not a gap. The wellbeing-wheel/restraint posture applies: the scanner never flags no-ai apps; neither should you.

## The pass

### Phase 0 — Mechanical scan (always first)

No daemon needed — pure file I/O, safe anytime.

```bash
python scripts/check_ai_native.py            # summary + dark list
python scripts/check_ai_native.py --full     # per-app table, AI apps ranked by score
python scripts/check_ai_native.py --json     # machine envelope (data.tiers, data.dark)
```

Exit code = dark-AI + split-chrome count (advisory; registered in `scripts/preflight.py --scope apps`, gate=False). Compare the headline numbers (tier counts, avg score) against the previous report in `insights/outputs/` — **a finding that recurs unaddressed is stronger than a fresh one** (deep-research baseline move).

### Phase 0.5 — Split AI chrome (deterministic, no judgment needed)

The chip scan ORs across every page in the app, so a multi-page app whose pill
lives on a *secondary* page reads as "has chip" while its primary surface —
the page nearly every visit lands on — spends the user's budget with no signal.
The `split AI chrome` section names those apps (kb was one: pill on
`docs.html`, absent from `/kb/`, found by hand in the 2026-07-11 UI audit).

Precise by construction: it fires only when the author demonstrably knows the
chip is required (they mounted one elsewhere) yet `pages/index.html` + the
sibling `.js` it loads carry none. Zero hits on a healthy tree — so treat any
hit as real. Fix by mounting `EOS_UI.modelPill` on the primary surface, or, if
the primary page genuinely has no AI path (an index that is only a chooser),
opt out **at the call site**:

```html
<!-- ai-native: ignore split-chrome (index is a chooser; AI lives in the tabs) -->
```

An inline marker beats a central allowlist here — a new legitimate case
shouldn't break the build (`.claude/rules/audits.md`).

### Phase 1 — Triage the dark list (judgment)

For each `dark` app, read enough of its `app.py` + pages to place it in exactly one bucket:

| Bucket | Signal | Action |
|---|---|---|
| **True dark** | AI output renders on the app's own page, looks hand-authored | Gap — rank it (Phase 2) |
| **Vault-writer** | think output lands only in the vault (`author:` frontmatter / `outputs/` covers provenance) | Add to `DARK_OK` in the scanner with a why-comment |
| **Background/cron** | think runs in a scheduler/reactor path, no user-facing render | `DARK_OK` with comment |
| **Consumed elsewhere** | output surfaces on another app's page (that app owns the chip) | `DARK_OK` with comment, name the consumer |
| **Hand-rolled UI** | has an AI affordance but not via shared helpers (e.g. bespoke ✨ button) | Migration candidate — swap to `EOS_UI.*` on touch |

`DARK_OK` is the scanner's allowlist (mirror of `DESKTOP_ONLY` in `tests/test_sys_mobile.py`) — every entry needs a trailing comment. This is the false-positive discipline from `.claude/rules/audits.md`: tune the heuristic against healthy apps rather than shipping a "23 apps are broken" report.

### Phase 2 — Rank the gaps

Order by leverage, not by count:

1. **Platform-fix candidates first** (`feedback_platform_fix_for_n_app_bugs`): if ≥3 apps share the same gap shape (e.g. think-without-provenance), the fix is shared plumbing — e.g. auto-chip any API response carrying `_provenance` — not N page edits. Name the platform seam.
2. **Reach gaps on daily-use apps**: apps with real verbs users would speak/slash (recipes, podcast, jobs, finance shape) missing `[[provides.verbs]]`. One manifest row each; migration is all-or-nothing per (app, surface) — see `.claude/rules/verb-registry.md`.
3. **modelPill on money-spending pages**: apps whose page triggers paid think calls with no cost visibility.
4. **✨ field-suggest on creative inputs**: only where `.claude/rules/field-suggest.md`'s when-NOT table permits (open/creative/generative fields, vault grounding exists).
5. **Cosmetic**: chips mounted without think (pill on a page that never calls it), FS declarations backed by heuristics. Fix on touch.

### Phase 3 — Report

Write the full report to `{vault}/30_Resources/EmptyOS/insights/outputs/YYYY-MM-DD-ai-native-audit.md` (long output to a file, never inline): pattern-catalog delta since last run, tier counts + trend, per-app matrix (`--full` output), triaged dark list, ranked gaps. Chat gets the headline numbers + top 5 gaps + the file path.

Grade evidence per `.claude/rules/deep-research.md`: scanner rows are `counted`; every dark-list triage decision is `read-verified` (you opened the app); anything else is `inferred` and must say so.

### Phase 4 — Fix (only on explicit ask)

This skill is an audit; don't start migrating without the user choosing scope. When they do:

- Chip/pill/✨ mounts and manifest verb rows follow the reference impls named in the relevant rule (`.claude/rules/{model-pill,field-suggest,verb-registry,voice-intents}.md`).
- Verb migrations are **all-or-nothing per (app, surface)** — include every verb of that surface or the omitted ones silently drop.
- New platform seams (e.g. auto-provenance) are their own session with a dark flag (`project_feature_pipeline_flag_default_dark`).
- Re-run Phase 0 after; the dark count should drop and `DARK_OK` should not have grown without comments.

## See also

- `scripts/check_ai_native.py` — the deterministic substrate (edit `DARK_OK` there)
- `.claude/skills/eos-architecture-review` — whole-system health; run this skill from its Step 6.5 when the AI lens is wanted
- `.claude/rules/model-pill.md`, `.claude/rules/field-suggest.md`, `.claude/rules/verb-registry.md`, `.claude/rules/voice-intents.md` — per-mechanism adoption contracts
- `docs/FRONTEND-DESIGN-LANGUAGE.md` §6 + `eos-design-system-audit` DL-7 — visual treatment of AI surfaces (the *how it looks* sibling of this skill's *is it there*)
- `{vault}/30_Resources/EmptyOS/insights/outputs/2026-07-10-ai-native-audit.md` — the founding audit + platform pattern catalog
