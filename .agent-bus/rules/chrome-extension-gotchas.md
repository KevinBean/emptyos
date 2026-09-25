---
paths:
  - "tools/chrome-extension/**"
---

# Chrome Extension Gotchas (MV3)

Split out of `dev-gotchas.md` (2026-09-25). Read before any extension timing
or content-script work.


- **The service worker is torn down after ~30s idle, and a pending `fetch` does NOT count as activity.** A model call that takes longer answers into a worker that no longer exists: the daemon does the work and caches it, and the page waits on a `chrome.runtime.sendMessage` promise that never settles *and never rejects*. Symptom that names it: **"only the fastest provider works"** (openai-mini 5s fine, claude-cli 33s hangs, a cold local model hangs). Fix: tick any extension API (`chrome.runtime.getPlatformInfo()`) every 20s while a request is in flight — see `keepAliveStart` in `tools/chrome-extension/background.js`. Bound the page's wait too, so a reply that never comes becomes a stated failure rather than a silent forever.
- **Playwright keeps the service worker alive** (it attaches a debugger), so a browser walk **cannot reproduce worker teardown**. A 40s-call test passed there while a real browser hung, and the green was used to argue against the user's own evidence. Any extension timing bug must be verified in a real browser, not in the walk. See `[[feedback_harness_that_cannot_reproduce_lies]]`.
- **`mcp__claude-in-chrome__left_click_drag` dispatches `pointermove` only — no `pointerdown`, no `pointerup`.** So it cannot drive any gesture built on pointer events, which on a `setPointerCapture` drag primitive means the gesture never starts and the drop logic never runs. It reports "Dragged from (x,y) to (x,y)" either way. This produced a confident, wrong "the mechanism is broken" reading of a canvas link bug on 2026-08-17; the real defect was a 9px drop target, and the diagnosis only held up once the handler was driven directly. Verify a pointer gesture with Playwright's `page.mouse.down/move/up` (which does emit them), or by dispatching `PointerEvent`s at the origin element. Same family as the Playwright/service-worker line above — a harness that cannot reproduce the thing under test does not return a null result, it returns a false one.
- **A content script lives in an isolated world**, so a global it sets is invisible from the *page* console — which is exactly where someone will look for it. Expose debug state over a message (`chrome.tabs.sendMessage(tabId, {type: 'EOS_READING_STATE'})`, read from the extension's own service-worker console), not as `window.__X`.
- **Ordering matters and fails silently**: a content script that reads a global another script sets must be listed *after* it in BOTH `registerContentScripts` and the `executeScript` fallback (`browser-session.js`). Miss one and the layer goes dormant with no error at all.

