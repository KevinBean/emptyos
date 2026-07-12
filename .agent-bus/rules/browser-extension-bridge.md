# Browser-Extension Bridge — select-on-page → daemon capability → in-page card

The EmptyOS Chrome extension (`tools/chrome-extension/`) is a **capability-to-browser
bridge**: it turns *any selection / page / link / image in the browser* into a call to a
**local daemon** endpoint, and renders the result back **in-page** — no navigating away, no
copy-paste into an app. The browser is where the user *encounters* content on the web; this
bridge lets EmptyOS act on that content in situ.

**Reference implementations (2 verbs, chrome-ext v0.8):** `dict-lookup` (right-click a word →
`GET /dictionary/api/lookup` → auto-save with reading context → in-page definition card) and
`eval-job-sel`/`eval-job-page` (right-click a selection or a LinkedIn/Seek page →
`POST /jobs/api/discover/evaluate {text}` → in-page match-score card). Both render through the
shared `renderResultCard`. Files: `tools/chrome-extension/{background,content}.js`.
Verdict/lineage: `project_immersive_dictionary_borrow_verdict`.

## The three pieces (+ one optional)

Every selection-verb is the same shape. To add one, you touch these and nothing else:

1. **Context-menu registration** (`background.js`, `chrome.runtime.onInstalled`) — one
   `chrome.contextMenus.create({ id, title, contexts })`. `contexts` is `["selection"]`,
   `["page"]`, `["link"]`, or `["image"]`. Add `documentUrlPatterns` to scope a verb to
   specific sites (as `capture-job` / `digest-video` do).
2. **Handler** (`background.js`, `chrome.contextMenus.onClicked`) — a branch on
   `info.menuItemId` that reads `getConfig()` (host + token), does the daemon `fetch` with
   `authHeaders(token)`, and reports. **All work rides the local daemon** — the extension
   holds no logic beyond scraping + dispatch. That is the privacy story (see below).
3. **Result surface** — either a `flashBadge(ok)` (fire-and-forget) or an **in-page card**
   via `showDictCard`/`renderDictCard` (when the user wants to *see* the answer). The card
   renderer is injected with `chrome.scripting.executeScript({ func, args })`, so it must be
   **fully self-contained** (stringified — closes over nothing but its arg) and must **escape
   every interpolated value** (`esc()` over `& < > "`) before `innerHTML`.
4. **(optional) reading-context capture** — a self-contained extractor in `content.js`
   (`window.EOS_SITE.<name>`), called via `callSiteExtractor(tabId, name)`, that returns
   `{ selection, sentence, source, page_title }`. `extractSelectionSentence` narrows the
   enclosing block to the one sentence containing the selection; `extractSelectionForKB`
   grabs the whole paragraph. Pass the result into the daemon call so the saved artifact
   links back to where it was found ("抓词现场·一键回跳"). The daemon endpoint must accept
   the context fields (as `dictionary.save_word` now takes `source_url`/`sentence`).

## The recipe (add a selection-verb in ~30 lines)

```js
// 1. register (onInstalled)
chrome.contextMenus.create({ id: "explain-sel", title: "Explain with EmptyOS",
                             contexts: ["selection"] });

// 2. handle (onClicked)
if (info.menuItemId === "explain-sel") {
  const ctx = await callSiteExtractor(tab.id, "extractSelectionSentence");  // optional
  const r = await fetch(host + "/assistant/api/explain", {
    method: "POST", headers: authHeaders(token),
    body: JSON.stringify({ text: info.selectionText, source: ctx?.source }),
  });
  const data = r.ok ? await r.json() : null;
  await showDictCard(tab.id, { word: "Explanation", lookup: { definition: data?.text || "" }, saved: r.ok });
  flashBadge(r.ok);
  return;
}
```

The daemon side is just an `@web_route` on an existing app — no new capability, no kernel
change. The bridge is app UX, not a kernel verb.

## Privacy / consent (load-bearing)

- The extension calls **`http://localhost:9000`** (configurable in options), authenticated
  with the daemon's bearer token. Content never goes to a third party *from the extension* —
  it goes to the user's own daemon.
- **Cloud consent still applies daemon-side.** If the endpoint's capability routes to a cloud
  provider (a `think` call landing on OpenAI), the daemon's consent gate fires as normal
  (CLAUDE.md Rule 18). The bridge doesn't bypass it. When the endpoint routes to a **local**
  provider (ollama, NLLB `translate`), the whole round-trip is on-device — *stronger* privacy
  than any cloud-backed web extension.
- Rule 19 (no vault data to cloud) is unaffected: the extension sends *page* content the user
  selected, never vault content.

## SDK / generalization triggers

- **`renderResultCard(payload)` — DONE (generalized at the 2nd consumer, v0.8).** Payload is
  `{ title, subtitle?, badge?:{text,color}, rows:[{value,tone?}], footer? }`, `tone ∈ default |
  muted | zh | quote | gap`. Both `dict-lookup` and `eval-job` build payloads for it. A 3rd verb
  reuses it as-is; extend the `tone` map (not the function shape) if a new row style is needed.
- **`extractSelectionSentence` / `extractSelectionForKB`** are already two consumers of the
  "climb to a block, extract context" shape — if a third wants it, factor a shared
  `_enclosingBlock(sel, tags)` helper in `content.js`.
- **Don't** build a generic "any-app-any-method" bridge verb — each verb is a deliberate,
  named affordance the user recognizes in the right-click menu. A catch-all is a worse UX and
  an unreviewed-action surface.

## Candidate selection-verbs (the menu — build on demand, not speculatively)

Ranked by fit to Kevin's actual priorities (NIW/career + wellbeing-wheel thin dimensions,
CLAUDE.md Rule 16). Most reuse the in-page card + an existing app endpoint (cheap); a few
need a new `@web_route`. **These are a menu, not a backlog** — build one when a real reading
habit calls for it.

| Verb (right-click …) | Daemon call | Why / dimension | Cost |
|---|---|---|---|
| **✅ SHIPPED v0.8 — Evaluate this job vs my CV** (selection on any page, or LinkedIn/Seek page auto-scrape) | `POST /jobs/api/discover/evaluate {text}` → score card | occupational · **NIW/career** — instant fit score on any posting | shipped |
| **Look up this engineering term in my KB** | `/kb/api/references` citation index | intellectual · **NIW-adjacent**, dogfoods his own IEC/cable KB | endpoint exists + card |
| **Add to calendar / remind me** (select "meeting Tue 3pm") | `think` parse → `calendar`/`reminders` add | occupational — turn any web date into a real event | small new endpoint |
| **Save this address to places / map it** | `geocode` → `places.add` | **environmental (thin)** | endpoint exists + card |
| **Add this person to people** (bio/name) | `people.add` | **social (thin)** — networking | endpoint exists |
| **Explain / TL;DR this** (select a dense paragraph) | `assistant`/`think` | intellectual (dominant — useful but fattens) | small endpoint + card |
| **Translate this in-page** | `translate` (NLLB, local) | intellectual · bilingual, competitor-parity, fully offline | endpoint exists + card |
| **▶ Play pronunciation** (button on the dict card) | `speak` TTS → audio in-page | intellectual · cheap card enrichment (真人发音) | card-only add |
| **Log this price to expense** (select "$45.20") | `expense.add` | **financial (thin)** | endpoint exists |

Wheel note: the thin-dimension verbs (places/people/expense) and the NIW-aligned ones
(job-eval, KB-term) are the higher-value adds; the explain/translate verbs are pleasant but
feed the already-dominant intellectual dimension — build those only if a real habit wants
them, not because they're easy.

## When NOT to reach for the bridge

- **The action has no web-content trigger** — if the user isn't acting on something *on a
  page*, it belongs in the daemon UI / CLI / companion rail, not a right-click verb.
- **Fire-and-forget with no result to show** — use `flashBadge`, skip the in-page card. Not
  every capture needs a card.
- **A verb per app method** — the menu is a curated set of recognizable affordances, not a
  reflection of the API surface. Add a verb when a *reading habit* wants it.
- **Ambient / always-on** (hover-to-lookup, hard-word highlighting, auto-collect 生词流) — a
  different mechanism (a static `content_scripts` on `<all_urls>` with its own lifecycle),
  deferred in `docs/DEFERRED-WORK.md`. This bridge is *explicit* (right-click) by design.

## Cross-references

- `tools/chrome-extension/README.md` — the extension's own feature log (V0.x history).
- `.claude/rules/standalone-distribution.md` — the *other* browser target (MV3 app you
  navigate TO); do not confuse — that bundles an EmptyOS app, this bridges arbitrary pages to
  the daemon. A standalone-target app must never gain host permissions; the bridge is where
  host access + cross-site scraping legitimately live.
- `.claude/rules/deep-link-to-app.md` — the daemon-side "open the answer in its real tool with
  state loaded" idea; the bridge's reading-context capture is its web-side complement.
- `project_immersive_dictionary_borrow_verdict` (memory) — the borrow verdict that produced
  the v0.7 upgrade and this rule.
