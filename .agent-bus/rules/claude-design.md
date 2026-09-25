---
paths:
  - "plugins/claude-design/**"
  - "apps/**/designer/**"
---
# Claude Design — claude.ai/design as an EmptyOS connection + capability

Claude Design (`claude.ai/design`) is a hosted **canvas** for static HTML design
files. EmptyOS integrates it two ways: (1) **conversation mode** — Claude Code uses
the harness `DesignSync` tool directly; (2) **the daemon** — the `plugins/claude-design/`
connector exposes it as a named **service** (`kernel.services.get("claude-design")`:
`list_projects`/`create_project`/`list_files`/`pull`/`push`) plus a cloud **provider**
on the `artifact` capability's `cloud-explicit` slot. This file is the architecture +
the standing usage rule.

## Transport — daemon-callable via the Claude Code CLI bridge (spike-proven 2026-06-22)

The earlier belief that "the daemon cannot call Claude Design" was **wrong**, and is
corrected here. There is no public HTTP API and Claude Design is not an MCP server, but
a **headless `claude -p --allowedTools "DesignSync"` carries the user's claude.ai design
auth** — spike-verified: it listed the real projects, and the transport's stream-json
parser extracted them end-to-end. So:

- `plugins/claude-design/transport.py::CliTransport` spawns `claude -p` via the
  `agent-runtime` bridge (`--output-format stream-json`), and
  `extract_designsync_result()` parses the **actual `DesignSync` tool_result** (not the
  model's prose) out of the stream. A native HTTP/MCP transport can replace it later
  behind the same `Transport` protocol.
- **Dark by graceful enhancement**, NOT forbidden: the provider's `available()` ties to
  `transport.ready()` — True on a local box with claude-cli authed, False on a headless
  VPS (no CLI/auth). The `host="https://claude.ai"` ⇒ `is_cloud` ⇒ the consent gate fires
  automatically. This is the `openai-image` pattern, not the anti-pattern the old text
  feared. The `artifact` provider is registered in the **opt-in `cloud-explicit` domain**
  so it never shadows local `viz`; one-shot generation `raise`s NotImplementedError until
  built (the round-trip service is the real surface).
- **Still do not** treat canvas HTML as committable source. A card is a static snapshot of
  *one* theme state with no behaviour — it can never *be* an EmptyOS `pages/` page or
  *produce* the `EOS_UI` JS the daemon serves; it is a visual spec you translate back.

## Default verdict (sharpened 2026-06-22) — build in EmptyOS, not on the canvas

The user's standing requirement: **deliverables live in EmptyOS** (a daemon-served
EOS_UI page, an app surface, or a PDF from `sdk/pdf.py` / the `business-case`
pipeline) — never parked on `claude.ai/design`. Under that requirement the
canvas's value is **marginal**, because:

- The canvas can only ever produce *static HTML I'd then have to translate* into
  EOS_UI — a detour, not a pipeline.
- The faster loop already exists: conversation mode + the `designer` app generate
  *real* EOS_UI components against KB priors, locally, served by the daemon.
- The one niche the canvas seemed to win (outbound static export → PDF/HTML) also
  fails the requirement, since those deliverables are wanted *in* EmptyOS too.

**So the default is: build the page/deliverable directly in EmptyOS.** Reach for
the canvas only in the narrow case where a human specifically wants hand
drag-and-drop direct manipulation on a genuinely static artifact that will NOT be
served from the daemon — a rare case for this user. When in doubt, skip it.

## Direction of fit — canvas is a design *input*

Your real UI is `EOS_UI` JS factories + daemon-served `pages/` + `theme.css`,
switchable across the themes in `theme.css`, with auto-UI / hash-routing / hub
+ settings panels. Claude Design produces static HTML cards. The one workflow
that works is **canvas as spec, Claude as the builder**:

```
design on canvas (drag/resize, real tokens)
   → read it back via DesignSync get_file
   → implement it as a proper EOS_UI page in the real stack
```

The canvas is the napkin sketch with the real palette; the translation to
`EOS_UI.*` + `pages/index.html` + theme tokens is the actual build, done in the
codebase the daemon-served way. This is the `FRONTEND-DESIGN-LANGUAGE.md`
discipline (design source → faithful EOS_UI implementation), not a copy.

## When to reach for it

| Use | Fit | Why |
|---|---|---|
| Outbound **static** pages — `promote` marketing page, FDE client deliverable, `business-case` one-pager, a landing page | ★ strongest | Genuinely static HTML; export (HTML/PDF/PPTX/Canva) IS the deliverable. No runtime twin, so no drift. |
| Drafting a **new app's layout** before building it | ★ good | Drag-tune against real tokens, then implement as EOS_UI. |
| Living **style reference** / sharing the kit | ★ ok | Eyeball-able showcase of the design DNA. |

## When NOT to

- **Daemon-served app pages** — they need EOS_UI + behavior + theme-switching the
  canvas can't hold. You'd maintain two drifting copies.
- **Replacing the `designer` app** — that already generates against design-system
  KB priors, in-vault, local-first (`.claude/rules/artifact-element-edit.md`).
  Claude Design is a cloud *complement* for direct-manipulation outbound work,
  not a replacement. Its only unique value over the EmptyOS stack is hand
  drag/resize/recolor for static artifacts.
- **Mirroring all of `EOS_UI`** — `EOS_UI` is ~40 JS-factory components with no
  preview files; each canvas card is hand-authored. Push a *slice for a purpose*,
  never a full mirror (the maintenance/drift cost outweighs it).

## Pushing a slice — the mechanism

`/design-sync` (or `DesignSync` directly): `list_projects` → build standalone
HTML cards locally (each: first-line `<!-- @dsCard group="…" -->`, real
`theme.css` hex values inlined so it renders without the daemon) → `create_project`
→ `finalize_plan` (you approve the exact path list + source dir) → `write_files`
(reads from disk; contents never enter model context). Incremental,
one-component-at-a-time, never wholesale-replace.

## Existing project (2026-06-22)

- **EmptyOS UI Kit** — `projectId dfaf23a7-089d-434b-9c1b-0440e19ca553`.
- 5 proof cards: `tokens` (Foundations) + `buttons` / `badges` / `modal` /
  `cards` (Components), carrying real `theme-eos` tokens.
- Local source: `D:/emptyos/.design-sync/eos-ui-kit/` (untracked).
- Verdict: round-trip works, but per the sharpened verdict above it serves no
  EmptyOS purpose — a harmless showcase only. Safe to delete (`DesignSync
  delete_files` / the canvas UI) without losing anything; the local source under
  `.design-sync/` is the reference if ever needed.

## Cross-references

- `docs/FRONTEND-DESIGN-LANGUAGE.md` — the visual DNA any implementation follows.
- `.claude/rules/artifact-element-edit.md` + the `designer` app — the EmptyOS-native,
  local-first design loop this complements (does not replace).
- `apps/.../promote` + `eos-fde-engagement` skill — the natural first consumers
  (outbound, static, brand-seeded).
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` — the "external tooling = ideas/inputs,
  the daemon stays the source of truth" ethos this rule embodies.
