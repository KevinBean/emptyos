---
name: eos-artifact-render-check
description: Render a published-artifact HTML file in a headless browser and inspect it before shipping — across both themes and phone width — to catch layout bugs that are invisible in the source. Use when the user says "check the artifact", "does this render", "look at it before I publish", "check both themes", or "does it work on a phone", or before publishing any standalone HTML page. NOT for EmptyOS daemon-served app pages (use eos-ui-walk for flows, eos-page-design-review for aesthetics, eos-design-system-audit for compliance).
---

# Artifact Render Check

An Artifact is not a daemon-served page. It has no `/static/`, cannot reach
`eos-components.css` or `EOS_UI`, runs under a strict CSP, gets wrapped in a
`<!doctype html><head>…</head><body>` skeleton it does not control, and renders
in **three** theme states. So the checks that keep app pages honest — tag
balance, `node --check`, a green scanner — all pass while the page on screen is
visibly broken.

**This skill exists because that happened three times in one session.** Every
bug below shipped past a clean structural check:

| Bug | What source-level checks said | What the render showed |
|---|---|---|
| CSS Grid list items | balanced tags, valid CSS | a 1,500px column of one character per line |
| Panel `id` doubling as hash target | valid HTML | reload anchor-jumped past the masthead |
| Active tab in an `overflow-x` strip | valid CSS | on a phone, no indication which panel was live |

The first is the one worth memorising, because it looks like correct CSS:

> In CSS Grid, **every child _element_ becomes its own grid item.** Only
> contiguous runs of bare text merge into a single anonymous item.

So `<li><strong>Book today</strong> — she asked for soonest</li>` inside a
`grid-template-columns: 1.6rem 1fr` list produces **three** items, not two. The
trailing text lands in the 1.6rem marker column and wraps one character per
line. Use `position:relative` + `padding-left` with an absolutely-positioned
`::before` for any marker list whose items contain inline elements.

## When to use

- Before the **first** publish of any artifact with real layout — grids, tabs,
  sticky bars, multi-column, anything beyond a plain document.
- Before a **republish** that changed structure or CSS.
- When the user says the format/layout is wrong. Do not guess from source —
  render it and look.

## When to skip

- A pure-prose artifact with no custom layout.
- A Markdown publish (no CSS of yours to break).
- A one-line copy edit that touched no markup.

## Prerequisites

- Playwright available — either the `playwright` MCP tools, or
  `pip install playwright && playwright install chromium`
- Python on PATH for the wrapper + the throwaway `http.server`
- Node on PATH if the artifact carries a `<script>` (`node --check`)
- A free localhost port (8777 is fine) — **not** 9000/9001/9002+, which belong
  to the daemons (`.claude/rules/daemon-handling.md`)
- A scratchpad directory for the wrapper, the preview, and the screenshots

No daemon required. This skill renders a **file**, not a served app — it never
touches `:9000`, and it must not be pointed at one.

## The harness

`file:` is blocked by the Playwright MCP, so the file must be served over HTTP.
And it must be rendered **wrapped**, the way the publisher wraps it — an
unwrapped fragment renders in quirks mode and lies to you.

```python
# wrap.py — mimic the publish-time skeleton
body = open(SRC, encoding="utf-8").read()
open(OUT, "w", encoding="utf-8").write(
    '<!doctype html>\n<html><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<style>*{margin:0;padding:0}</style></head><body>\n' + body + '\n</body></html>'
)
```

```bash
cd <scratchpad> && python -m http.server 8777 --bind 127.0.0.1   # run_in_background
```

Then navigate, screenshot, read the image. **Stop the server and delete the
screenshots when done** — Playwright writes them to the repo root.

To render a specific theme, stamp the wrapper's `<html>`:
`<html data-theme="dark">`.

## The checklist

Run all of it. Each row has caught a real bug.

| Check | How | Looking for |
|---|---|---|
| **Structural** | tag-balance count per block element | unclosed `div`/`section`/`li` |
| **JS syntax** | extract `<script>` to a real file, `node --check` | parse errors |
| **Light, desktop** | 900px viewport, full-page screenshot | the obvious |
| **Full-page height** | compare px height before/after a fix | a collapse or explosion is the cheapest regression signal there is |
| **Dark** | `data-theme="dark"` on the wrapper | any colour defined only inside a media/`[data-theme]` block |
| **System dark** | `prefers-color-scheme` with no stamp | the un-stamped third state most designs forget |
| **Phone** | 375px, full-page | horizontal overflow, off-screen active state, collapsed columns |
| **Deep links** | navigate to each `#hash` | wrong panel, anchor-jump past the header |
| **Keyboard** | shortcuts, focus ring | hijacked keys, invisible focus |

Full-page pixel height deserves its own line: on the grid bug, **11,610px →
8,805px** was the proof the fix landed, and it took one number to see it.

## Traps

- **`file:` is blocked.** Serve over `127.0.0.1`.
- **The browser caches hard.** After every edit, re-navigate with a fresh
  cache-buster (`?v=2`, `?v=3`). An identical screenshot after a fix usually
  means a cached page, not a failed fix — check that before re-editing.
- **`node --check <(python -c …)`** fails on Windows (process substitution maps
  to a `/proc` path Node cannot open). Extract the script to a real file first.
- **Screenshots land in the repo root**, not the scratchpad. `rm` them.
- **Never patch the file through nested shell strings.** A heredoc through
  `bash -c` eats backslashes and apostrophes silently. Use `Edit`/`Write`
  directly — see `.claude/rules/dev-gotchas.md`.
- **Republish conflicts**: if another session published first, do not force
  blindly. Fetch the published copy, diff its content items against yours, and
  force only once you can show your file is a superset.

## Report

State what you rendered, what you found, and what the fix was — with the
before/after signal that proves it:

```
Rendered: light 900 · dark 900 · light 375 · 3 deep links
Fixed:    .checks grid → position/padding (grid-item wrap)
Proof:    page height 11,610px → 8,805px
Clean:    tags balanced · node --check OK · no h-overflow at 375
```

## Cross-references

- `artifact-design` skill — load **before** authoring; this one runs after.
- `.claude/rules/dev-gotchas.md` — nested-string patching, Windows quirks.
- `.claude/rules/audits.md` — the graduation discipline this skill came from.
- `eos-screenshot` — produces redacted article images; different job.
- `eos-page-design-review` / `eos-ui-walk` — daemon-served app pages, not artifacts.
