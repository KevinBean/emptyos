---
paths:
  - "skills/tool-*/**"
  - ".claude/skills/tool-*/**"
  - "plugins/playwright/**"
  - "apps/public/labs/operate/**"
---

# Web-Tool Operation — driving a site that has no API and no MCP server

Some of the most capable tools available to EmptyOS have **no API, no MCP
server, and no CLI** — Google Flow, ChatGPT web, Gemini web, NotebookLM,
claude.ai. They are reachable only through a logged-in browser. This rule is
how we operate them as *tools* rather than as manual detours.

First worked example: `skills/tool-flow-stills/` (Google Flow, 2026-08-07).
That session produced 18 film stills plus a batch colour-correction pass, and
cost far more than it should have because none of the rules below were known.

## The single most important rule

**If the site has a chat/agent panel, give it batch instructions. Do not
puppet its UI one item at a time.**

Flow's project chat applied the same colour correction to **11 images in one
request**, correctly, in about a minute — after hours of hand-editing frame by
frame. The site's own agent has privileged access to its assets; browser
automation does not. Driving the UI is the fallback, not the method.

Prompt shape that works:

> Apply this to MULTIPLE existing items — **edit the existing ones, do not
> create new ones**. Take every item whose name matches ⟨pattern⟩ and apply:
> ⟨the change⟩. Keep ⟨invariants⟩ unchanged. Can you do them all in one go?
> If not, do as many as you can and **tell me which ones you completed**.

Three load-bearing clauses: *edit, don't generate* (or it makes new items); a
**name pattern as selector** (see below); and *report what you completed* (so
you get a manifest instead of auditing by eye).

## Naming is a selector, not a label

Rename assets to a machine-addressable convention (`NN · description · meta`).
This looks like housekeeping and is actually the **query language** — it is what
lets a batch instruction say "every item whose name starts with a number and a
middle dot", and it makes a search on the separator return exactly the keeper
set, with duplicates excluded. Do this *before* any batch work.

## Verification discipline

These cost real hours on 2026-08-07:

- **A dark/blank render is usually a LOADING PLACEHOLDER, not a failure.**
  Judged two good edits as destroyed and reverted them, then wrote the wrong
  rule into a skill. Wait for the render to settle, and check the version-history
  panel — the finished result appears there while the canvas still shows the
  placeholder.
- **Never judge a set from thumbnails.** Two defects (a frame set in the wrong
  country, a continuity break between two shots of the same subject) were
  invisible in the grid and obvious full-screen.
- **Never judge progress from a proxy signal.** A "has versions" badge is not
  "has the change I wanted" — most of those versions were a *wrong* edit. Read
  the artefact, not the badge. This produced two wrong progress reports in one
  session.
- **Grids reflow asynchronously.** Fixed coordinates go stale unpredictably;
  clicks then land on empty space and typed text silently lands in the *page's*
  chat box instead. Screenshot to confirm state before every click in a loop —
  a blind loop skips items without telling you.
- **Destructive actions get their own verification.** Read the item's name in
  the same screenshot you act from. Prefer trash-with-restore over delete, and
  never empty the trash.

## Tool choice (researched 2026-08-07)

| Tool | Use it for | Not for |
|---|---|---|
| **Claude in Chrome** (current) | Anything needing the user's **logged-in session** — Flow, ChatGPT, Gemini, NotebookLM, claude.ai. This is the only option that inherits real auth without credential handling. | Repeatable headless automation; anything needing network/perf inspection |
| **Playwright MCP** / the `playwright` plugin | Repeatable, scriptable, headless flows against **our own** surfaces — dogfood UI-walk, fix-agent repro, release smoke | Authenticated third-party SaaS (you'd be managing someone else's credentials) |
| **Chrome DevTools MCP** | Network/perf/console debugging | General driving — it costs ~18k tokens of tool definitions before doing anything |
| **Stagehand / browser-use** | `act`/`extract`/`observe` over natural language | — see verdict below |

**Verdict: do not add another browser stack.** Claude in Chrome plus the
existing `playwright` plugin already covers both axes (authenticated session /
headless repeatable). Stagehand and browser-use are frameworks for *building* an
agent that browses; we already have the agent. Adding one would duplicate the
plugin and re-introduce credential handling that the Chrome extension exists to
avoid. Revisit only if a genuine need appears for unattended authenticated
automation with no human present — which today it does not.

## Anthropic's own computer-use / browser-use tool (evaluated 2026-08-22)

Anthropic's *officially hosted* computer-use tool (GA'd multi-action-per-turn) and its new
browser-use tool are a different thing from the third-party frameworks rejected above — WE
still execute every action locally through our own executor; the tool only lets the model
batch several actions into one response instead of round-tripping per click. So the verdict
above doesn't directly rule it out. It's still not adopted: `apps/public/labs/operate/` already
grounds actions the way this tool is catching up to — its executor sends a screenshot **plus**
a UIA/uiautomator accessibility-tree snapshot, the model picks a stable `ref` from that tree
(never a raw coordinate when one exists), and only Android falls back to pixels. Adopting the
official schema would only cut round-trips on Anthropic-backed runs and would lock the loop to
one provider, with no felt need today. Deferred with a trigger, not rejected — see
`docs/DEFERRED-WORK.md` "operate executor: adopt Anthropic's official computer-use tool schema
for multi-action batching" and `docs/OPEN-SOURCE-BORROWING-PLAN.md` § Anthropic Claude Platform
(2026-08-22) for the full verdict.

## Generalising to other no-MCP sites

The pattern transfers to ChatGPT web, Gemini web / NotebookLM, and claude.ai.
Before writing a `tool-<site>` skill, establish these six facts — they are what
made the Flow skill useful, and none are guessable:

1. **Is there an agent panel that accepts batch instructions?** If yes, that is
   the interface. Everything else is fallback.
2. **What is free vs metered?** Flow's images are free and its video is 15
   credits; that single fact changes the whole workflow (review-and-regenerate
   becomes the cheap half).
3. **Where is the approval/consent gate, and what are its quirks?** Flow renders
   its credit-approval widget **once per conversation session**; later requests
   wait forever on a control that was never drawn. Free actions skip the gate.
4. **Is there version history / undo?** Determines whether edits are safe to try.
5. **What is the durable identifier?** Rename support, stable URLs, or nothing.
6. **What are the licence and watermark terms of the output?** Flow output
   carries SynthID with no opt-out — decisive if the work is commercial.

Log the answers in the skill. A skill without them is a list of clicks; with
them it is a tool.

### Site survey (2026-08-07)

The shape recurs, which is what makes the pattern worth having:

| | agent panel | asset store | container | scheduling |
|---|---|---|---|---|
| **Google Flow** | project chat ✓ **batches** | media grid, **renameable** | project | — |
| **ChatGPT** (Pro) | the chat itself ✓ | **Library** | **Projects** | **Scheduled** ✓ |
| Gemini / NotebookLM | chat ✓ | notebook sources | notebook | — |
| claude.ai | chat ✓ | Artifacts / Projects | Project | — |

Two things this survey already changes:

- **The batch instruction should transfer** — every one of these is an agent
  with access to its own store, which is exactly the condition that made Flow's
  11-image batch work. Test it the same way: name a selector, say *edit don't
  create*, ask for a completion manifest.
- **ChatGPT has `Scheduled`, which Flow does not.** That is a genuinely
  different capability: work that runs with nobody driving the browser. It sits
  outside this rule's "logged-in session is not a reliable unattended
  substrate" caveat, because the site itself is doing the running. Worth a
  dedicated look before building any polling loop of our own.

**Unverified, and the honest gap:** whether ChatGPT's Library supports renaming.
The whole naming-as-selector trick depends on it. If a site has no durable
per-asset identifier, batch instructions must select by *description* instead —
weaker, and likely to need a completion manifest to stay honest.

## When NOT to reach for this

- The site has an API, CLI, or MCP server → use that, always.
- The task is one-off and small → just do it by hand.
- The work needs to run unattended → a logged-in browser session is not a
  reliable substrate; move the work to an API-backed path.
- The action is irreversible and outward-facing (publish, send, pay) → those
  stay human-gated regardless of how good the automation is.

## Cross-references

- `skills/tool-flow-stills/` — the first worked example, with the Flow-specific
  mechanics
- `.claude/rules/browser-extension-bridge.md` — the *other* browser direction:
  bridging arbitrary pages **into** the daemon, rather than driving a web app
- `plugins/playwright/` — the headless, repeatable half
- `.claude/rules/proposed-action.md` — why irreversible actions stay gated
