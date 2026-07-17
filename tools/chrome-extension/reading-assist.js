// Adaptive reading layer. Statically present on web pages but completely dormant
// in Off mode: no observer, scan, highlight, injected UI, or model call exists
// until the reader opts in.
//
// Settings are NOT stored here. The dictionary app owns them; this asks the
// service worker, which asks the daemon. One source of truth, so the browser and
// /dictionary can never hold two different opinions about the reader.

(() => {
  if (window.__EOS_READING_ASSIST__) return;
  window.__EOS_READING_ASSIST__ = true;

  const DEFAULTS = globalThis.EOS_READING_DEFAULTS;
  // Every DECISION this file used to make inline now lives in reading-policy.js —
  // pure, and therefore actually tested. This file keeps what touches the world: the
  // DOM, the timers, the messaging, and the state itself. It owns the state; the
  // policy says what the state means.
  const POLICY = globalThis.EOS_READING_POLICY;
  const HIGHLIGHT_NAME = "eos-reading-words";
  const CARD_ID = "eos-reading-card";
  const RAIL_ID = "eos-reading-rail";
  const MAX_TEXT = 8000;
  const SCROLL_SETTLE_MS = 350;
  const MODEL_TIMEOUT_MS = POLICY.MODEL_TIMEOUT_MS;
  const SKIP = POLICY.SKIP;
  const PROSE = POLICY.PROSE;

  let settings = { ...DEFAULTS };
  let mode = "off";
  let observer = null;
  let scanTimer = null;
  let scrollTimer = null;
  let scanSerial = 0;
  let lastScanAt = 0;
  let items = new Map();      // lowercased word -> item
  let ranges = [];            // every highlighted range on the page
  let scanned = new Set();    // region fingerprints already analysed — never pay twice
  let scanning = false;       // a scan is in flight (the rail says so)
  let scanQueued = false;     // a scan is scheduled but has not fired yet
  let tooLittleText = false;  // this screen has nothing worth sending
  let queuedAt = 0;           // when it was FIRST queued — the debounce's ceiling
  let railError = "";         // the last failure, shown instead of a forever-"Reading…"

  // ── page text ───────────────────────────────────────────────────────────

  // The DOM walk is ours; the judgement is the policy's.
  function linkIsProse(link) {
    const block = link.closest(PROSE);
    if (!block) return false;
    return POLICY.linkIsProse({
      blockLen: (block.textContent || "").trim().length,
      linkLen: (link.textContent || "").trim().length,
    });
  }

  function eligibleTextNode(node) {
    const parent = node && node.parentElement;
    if (!parent || !node.nodeValue || !node.nodeValue.trim()) return false;
    if (parent.closest(SKIP)) return false;
    const link = parent.closest("a");
    if (link && !linkIsProse(link)) return false;
    const style = getComputedStyle(parent);
    return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
  }

  function textWalker() {
    return document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: node => eligibleTextNode(node)
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
  }

  function inViewport(rect, margin) {
    const pad = margin || 0;
    return rect.bottom > -pad && rect.top < innerHeight + pad && rect.height > 0;
  }

  // Only what the reader can actually SEE (plus a screen of lead-in). Scanning the
  // whole document would spend the paid call on text they may never reach, and a
  // long article's tail would never be analysed at all once the 8k budget ran out.
  function visibleText(margin) {
    if (!document.body) return "";
    const walker = textWalker();
    const chunks = [];
    let length = 0;
    while (walker.nextNode() && length < MAX_TEXT) {
      const node = walker.currentNode;
      const range = document.createRange();
      range.selectNodeContents(node);
      if (!inViewport(range.getBoundingClientRect(), margin === undefined ? innerHeight : margin)) {
        continue;
      }
      const value = node.nodeValue.replace(/\s+/g, " ").trim();
      if (value.length < 2) continue;
      chunks.push(value);
      length += value.length + 1;
    }
    // Live captions (video) are their own surface — short, and they churn.
    const caption = [...document.querySelectorAll(".ytp-caption-segment, [class*='subtitle' i]")]
      .filter(el => {
        const style = getComputedStyle(el);
        return style.display !== "none" && style.visibility !== "hidden";
      })
      .map(el => el.textContent.replace(/\s+/g, " ").trim())
      .filter(Boolean).join(" ").slice(0, 500);
    if (caption) chunks.unshift("Current caption: " + caption);
    return chunks.join(" ").slice(0, MAX_TEXT);
  }

  const fingerprint = POLICY.fingerprint;

  // ── highlights ──────────────────────────────────────────────────────────

  function clearHighlights() {
    ranges = [];
    if (globalThis.CSS && CSS.highlights) CSS.highlights.delete(HIGHLIGHT_NAME);
  }

  function findRanges(word) {
    const found = [];
    if (!document.body || !word) return found;
    const needle = word.toLowerCase();
    const walker = textWalker();
    while (walker.nextNode() && found.length < 4) {
      const node = walker.currentNode;
      const haystack = node.nodeValue.toLowerCase();
      let at = haystack.indexOf(needle);
      while (at >= 0 && found.length < 4) {
        const before = at === 0 ? "" : haystack[at - 1];
        const after = at + needle.length >= haystack.length ? "" : haystack[at + needle.length];
        if (!/[a-z'-]/i.test(before) && !/[a-z'-]/i.test(after)) {
          const range = document.createRange();
          range.setStart(node, at);
          range.setEnd(node, at + needle.length);
          found.push(range);
        }
        at = haystack.indexOf(needle, at + needle.length);
      }
    }
    return found;
  }

  // Ranges are located even where the CSS Custom Highlight API is missing: the rail
  // is built from them (a word's position on screen is what orders it), so bailing
  // out early would leave every item range-less and the rail permanently empty.
  function renderHighlights() {
    clearHighlights();
    for (const item of items.values()) {
      item._ranges = findRanges(item.word);
      ranges.push(...item._ranges);
    }
    if (ranges.length && globalThis.CSS && CSS.highlights && globalThis.Highlight) {
      CSS.highlights.set(HIGHLIGHT_NAME, new Highlight(...ranges));
    }
  }

  function absorb(list) {
    for (const item of list || []) {
      if (item && item.word) items.set(item.word.toLowerCase(), item);
    }
    renderHighlights();
    renderRail();
  }

  // ── the rail: every visible word, in reading order, updated on scroll ────

  function visibleItems() {
    const out = [];
    for (const item of items.values()) {
      const rects = (item._ranges || []).map(r => r.getBoundingClientRect())
        .filter(rect => inViewport(rect, 0));
      if (!rects.length) continue;
      out.push({ item, top: Math.min(...rects.map(r => r.top)) });
    }
    // Reading order — what the eye meets first comes first.
    out.sort((a, b) => a.top - b.top);
    return out.map(entry => entry.item);
  }

  function sourceChip(source) {
    if (source === "vault") return { text: "yours", title: "From your own saved note" };
    if (source === "cache") return { text: "saved", title: "A remembered explanation — no model call" };
    return { text: "new", title: "Freshly generated" };
  }

  function railRow(item) {
    const row = element("li", "eos-rail-row");
    row.dataset.word = item.word;

    const head = element("div", "eos-rail-head");
    head.appendChild(element("span", "eos-rail-word", item.word));
    const chip = sourceChip(item.source);
    const badge = element("span", "eos-rail-chip eos-rail-chip-" + (item.source || "model"), chip.text);
    badge.title = chip.title;
    head.appendChild(badge);
    row.appendChild(head);

    const meta = [item.part_of_speech, item.native || item.chinese].filter(Boolean).join(" · ");
    if (meta) row.appendChild(element("div", "eos-rail-meta", meta));
    row.appendChild(element("div", "eos-rail-meaning",
      item.meaning_in_context || item.definition || ""));

    const actions = element("div", "eos-rail-actions");
    if (settings.pronounce) actions.appendChild(pronounceButton(item));
    const save = element("button", "eos-rail-save", "Save");
    save.type = "button";
    save.addEventListener("click", async event => {
      event.stopPropagation();
      save.disabled = true;
      save.textContent = "Saving…";      // a strong model polishes the note first
      const result = await send({ type: "EOS_READING_SAVE", item, sourceUrl: location.href });
      save.disabled = false;
      save.textContent = result && result.ok ? "Saved" : "Failed";
      if (result && result.ok) {
        feedback(item, "saved");
        item.source = "vault";           // it is theirs now
        renderRail();
      }
    });
    actions.appendChild(save);
    row.appendChild(actions);

    // Clicking a row takes the reader to the word, rather than making them hunt.
    row.addEventListener("click", () => {
      const range = (item._ranges || [])[0];
      if (range) range.startContainer.parentElement
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
      showCard(item, null, { tier: item.source });
    });
    return row;
  }

  function renderRail() {
    if (mode !== "flow" || !settings.rail) {
      document.getElementById(RAIL_ID)?.remove();
      return;
    }
    let rail = document.getElementById(RAIL_ID);
    if (!rail) {
      rail = element("aside");
      rail.id = RAIL_ID;
      const head = element("div", "eos-rail-header");
      head.appendChild(element("span", "eos-rail-title", "Words here"));
      const count = element("span", "eos-rail-count", "");
      count.id = "eos-rail-count";
      head.appendChild(count);
      const collapse = element("button", "eos-rail-collapse", "–");
      collapse.type = "button";
      collapse.title = "Collapse";
      collapse.addEventListener("click", () => {
        const on = rail.classList.toggle("eos-rail-collapsed");
        collapse.textContent = on ? "+" : "–";
      });
      head.appendChild(collapse);
      rail.appendChild(head);
      const list = element("ul", "eos-rail-list");
      list.id = "eos-rail-list";
      rail.appendChild(list);
      document.documentElement.appendChild(rail);
    }
    const list = rail.querySelector("#eos-rail-list");
    const visible = visibleItems();
    list.replaceChildren();
    // What the rail may CLAIM is the policy's call — it is the rule that was wrong
    // three times today, and it is now the one thing here that is unit-tested.
    const verdict = POLICY.railState({
      visibleCount: visible.length, itemCount: items.size,
      scanning, scanQueued, tooLittleText, railError,
    });
    if (verdict.kind === "rows") {
      for (const item of visible) list.appendChild(railRow(item));
    } else {
      const cls = "eos-rail-empty" + (verdict.kind === "error" ? " eos-rail-failed" : "");
      list.appendChild(element("li", cls, verdict.message));
    }
    rail.querySelector("#eos-rail-count").textContent = visible.length ? String(visible.length) : "";
  }

  // ── scanning ────────────────────────────────────────────────────────────

  async function runScan() {
    scanQueued = false;
    queuedAt = 0;
    if (mode !== "flow") return;
    const text = visibleText();
    if (!POLICY.hasEnoughText(text)) { tooLittleText = true; renderRail(); return; }
    tooLittleText = false;
    const region = fingerprint(text);
    if (scanned.has(region)) { renderRail(); return; }   // already paid for this view
    scanned.add(region);
    lastScanAt = Date.now();
    const serial = ++scanSerial;
    scanning = true;
    railError = "";
    renderRail();                                        // say we are working on it

    // The reader's OWN words first, and without waiting: they are already in the
    // vault, with a better gloss than a model would write, and meeting one in the
    // wild is the whole point of having saved it. Gating that behind a model that
    // might decide the screen is easy — and, on a slang page, did — threw away the
    // best moment in the loop. No model, no cost, no waiting.
    send({ type: "EOS_READING_KNOWN", text }).then(mine => {
      if (!POLICY.acceptResponse({ serial, currentSerial: scanSerial, mode })) return;
      if (mine && mine.ok && (mine.items || []).length) absorb(mine.items);
    });

    const response = await send({
      type: "EOS_READING_ANALYZE", text, url: location.href, title: document.title,
    }, MODEL_TIMEOUT_MS);
    // A newer scan owns the rail now, or the reader left Flow. The call was still
    // worth making; this reply is simply no longer the one being waited for.
    if (!POLICY.acceptResponse({ serial, currentSerial: scanSerial, mode })) return;
    scanning = false;
    if (response && response.ok) {
      absorb(response.items || []);
      return;
    }
    scanned.delete(region);                              // let a retry happen
    railError = (response && response.error) || "Could not reach EmptyOS.";
    renderRail();
  }

  function scheduleScan(delay) {
    if (mode !== "flow") return;
    if (scanTimer) clearTimeout(scanTimer);
    const plan = POLICY.scanDelay({
      now: Date.now(), lastScanAt, queuedAt, wasQueued: scanQueued, delay,
    });
    queuedAt = plan.queuedAt;   // the moment the reader began waiting — the ceiling
    scanQueued = true;
    scanTimer = setTimeout(() => { scanTimer = null; runScan(); }, plan.wait);
    renderRail();
  }

  function onScroll() {
    if (mode !== "flow") return;
    renderRail();                       // instant: re-list what is on screen now
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {    // deferred: only pay once scrolling settles
      scrollTimer = null;
      scheduleScan(0);
    }, SCROLL_SETTLE_MS);
  }

  function activateFlow() {
    if (!document.body) return;
    observer = new MutationObserver(mutations => {
      if (mutations.some(m => !document.getElementById(CARD_ID)?.contains(m.target)
                           && !document.getElementById(RAIL_ID)?.contains(m.target))) {
        scheduleScan(1800);
      }
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll, { passive: true });
    scheduleScan(250);
    renderRail();
  }

  function deactivate() {
    scanSerial += 1;
    if (observer) observer.disconnect();
    observer = null;
    if (scanTimer) clearTimeout(scanTimer);
    if (scrollTimer) clearTimeout(scrollTimer);
    scanTimer = scrollTimer = null;
    removeEventListener("scroll", onScroll);
    removeEventListener("resize", onScroll);
    items.clear();
    scanned.clear();
    scanning = false;
    scanQueued = false;
    tooLittleText = false;
    queuedAt = 0;
    // The throttle protects against paying twice for the SAME view. A teardown
    // just discarded every scanned region, so there is nothing left to pay twice
    // for — and carrying the old timestamp over would stall the next scan for up
    // to MIN_SCAN_INTERVAL with an empty rail claiming nothing was found.
    lastScanAt = 0;
    railError = "";
    clearHighlights();
    removeCard();
    document.getElementById(RAIL_ID)?.remove();
  }

  function applySettings(next, nextMode) {
    const before = settings;
    const beforeMode = mode;
    settings = { ...DEFAULTS, ...(next || {}) };

    // The per-site pause and the private-host default are re-applied HERE, from the
    // settings, rather than trusted from a broadcast that carries no per-tab mode.
    // This is the last gate before the page's text is read.
    const wanted = POLICY.effectiveMode({
      settings, nextMode, hostname: location.hostname.toLowerCase(),
    });

    // Tearing down is for a change that invalidates the words. A cosmetic one just
    // re-renders them — answering every panel tweak with a teardown is what threw
    // away answers the model had already been paid for.
    if (!POLICY.shouldRestart({ before, after: settings, beforeMode, wantedMode: wanted })) {
      mode = wanted;
      renderRail();
      return;
    }
    deactivate();
    mode = wanted;
    if (mode === "flow") activateFlow();
  }

  // ── word under the cursor ───────────────────────────────────────────────

  function wordAtPoint(x, y) {
    let range = null;
    if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(x, y);
      if (pos && pos.offsetNode?.nodeType === Node.TEXT_NODE) {
        range = document.createRange();
        range.setStart(pos.offsetNode, pos.offset);
        range.collapse(true);
      }
    } else if (document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(x, y);
    }
    if (!range || range.startContainer.nodeType !== Node.TEXT_NODE) return "";
    const text = range.startContainer.nodeValue || "";
    let start = Math.min(range.startOffset, Math.max(0, text.length - 1));
    let end = start;
    while (start > 0 && /[A-Za-z'-]/.test(text[start - 1])) start -= 1;
    while (end < text.length && /[A-Za-z'-]/.test(text[end])) end += 1;
    return text.slice(start, end);
  }

  // A word is usually wrapped in an inline tag (<b>, <em>, <a>, <span>) — exactly
  // the emphasised words a reader stops on. `parentElement.textContent` for those
  // is the WORD ITSELF, so sending it as "context" tells the model nothing and it
  // invents a sentence the page never contained. Climb to the enclosing block, then
  // narrow to the one sentence holding the word. Mirrors content.js's boundary set.
  const BLOCK_TAGS = ["P", "LI", "BLOCKQUOTE", "TD", "DD", "FIGCAPTION",
                      "H1", "H2", "H3", "H4", "H5", "H6", "DIV", "ARTICLE", "SECTION"];

  function enclosingBlockText(node) {
    let el = node;
    while (el && el.nodeType === 3) el = el.parentNode;
    let hops = 0;
    while (el && el.tagName && !BLOCK_TAGS.includes(el.tagName) && hops++ < 30) {
      el = el.parentNode;
    }
    return ((el && el.textContent) || "").replace(/\s+/g, " ").trim();
  }

  function sentenceAround(block, word) {
    if (!block) return "";
    const safe = word.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&");
    const re = new RegExp("\\b" + safe + "\\b", "i");
    const hit = block.split(/(?<=[.!?])\s+/).find(s => re.test(s));
    return (hit || block).slice(0, 420);
  }

  // ── card ────────────────────────────────────────────────────────────────

  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  function removeCard() {
    document.getElementById(CARD_ID)?.remove();
  }

  // Nothing may wait forever. If the service worker is torn down mid-request (it is
  // killed after ~30s idle, and a pending fetch does not stop that), the reply never
  // arrives AND the promise never rejects — the page just waits, and the rail says
  // "Reading this screen…" until the tab is closed. A bounded wait turns a silent
  // hang into a stated failure the reader can act on.
  async function send(message, timeoutMs) {
    const limit = timeoutMs || 0;
    try {
      const reply = chrome.runtime.sendMessage(message);
      if (!limit) return await reply;
      let timer = null;
      const expiry = new Promise(resolve => {
        timer = setTimeout(() => resolve({ error: "The model did not answer in time." }), limit);
      });
      try {
        return await Promise.race([reply, expiry]);
      } finally {
        clearTimeout(timer);
      }
    } catch (error) {
      return { error: error && error.message ? error.message : "EmptyOS extension unavailable" };
    }
  }

  // Feedback carries the whole item, not just the word: "I know this" and "Still
  // hard" are both judgements the reader wants RECORDED in their vocabulary, and
  // the daemon cannot write a note from a bare word.
  async function feedback(item, action) {
    const word = typeof item === "string" ? item : item?.word;
    const payload = typeof item === "string" ? null : item;
    return send({
      type: "EOS_READING_FEEDBACK", word, action, item: payload,
      sourceUrl: location.href,
    });
  }

  function pronounceButton(item) {
    const button = element("button", "eos-reading-say", "▶");
    button.type = "button";
    button.title = "Hear it";
    button.addEventListener("click", async event => {
      event.stopPropagation();
      button.disabled = true;
      const result = await send({ type: "EOS_READING_PRONOUNCE", word: item.word });
      button.disabled = false;
      if (result && result.dataUrl) {
        try { await new Audio(result.dataUrl).play(); } catch (e) { button.title = "Playback blocked"; }
      } else {
        button.title = (result && result.error) || "Pronunciation unavailable";
        button.classList.add("eos-reading-say-off");
      }
    });
    return button;
  }

  function showCard(item, point, meta = {}) {
    removeCard();
    const card = element("aside");
    card.id = CARD_ID;
    let display = settings.display || "auto";
    if (display === "auto" && point && point.caption) display = "corner";
    if (!point) display = "corner";     // opened from the rail, not from the text
    card.dataset.display = display;
    card.style.setProperty("--eos-anchor-y",
      Math.max(12, Math.min((point?.y || 86) - 18, innerHeight - 300)) + "px");
    card.style.setProperty("--eos-anchor-x",
      Math.max(12, Math.min(point?.x || 12, innerWidth - 336)) + "px");

    const kicker = meta.loading ? "looking up…" : sourceChip(item.source || meta.tier).title;
    card.appendChild(element("div", "eos-reading-kicker", kicker));

    const title = element("div", "eos-reading-titlebar");
    title.appendChild(element("h2", "eos-reading-title", item.word || "Word"));
    if (settings.pronounce && !meta.loading) title.appendChild(pronounceButton(item));
    card.appendChild(title);

    const metaText = [item.part_of_speech, item.native || item.chinese].filter(Boolean).join(" · ");
    if (metaText) card.appendChild(element("div", "eos-reading-meta", metaText));
    card.appendChild(element("p", "eos-reading-meaning",
      item.meaning_in_context || item.definition || ""));
    if (item.definition && item.definition !== item.meaning_in_context) {
      card.appendChild(element("p", "eos-reading-definition", item.definition));
    }
    if (item.sentence) card.appendChild(element("p", "eos-reading-sentence", item.sentence));

    if (meta.loading) {
      document.documentElement.appendChild(card);
      return;
    }

    const actions = element("div", "eos-reading-actions");
    const close = element("button", "", "Close");
    close.type = "button";
    close.addEventListener("click", () => removeCard());
    actions.appendChild(close);

    for (const [action, label] of [["known", "I know this"], ["hard", "Still hard"], ["save", "Save word"]]) {
      const button = element("button", "", label);
      button.type = "button";
      button.dataset.action = action;
      button.addEventListener("click", async event => {
        event.stopPropagation();
        // All three verdicts write to the vocabulary — a strong model polishes the
        // note first, so this is a second or two, not instant. Say so.
        button.disabled = true;
        button.textContent = "Saving…";
        const result = action === "save"
          ? await send({ type: "EOS_READING_SAVE", item, sourceUrl: location.href })
          : await feedback(item, action);
        button.disabled = false;
        const saved = result && (result.ok || result.saved);
        button.textContent = !saved ? "Save failed"
          : action === "known" ? "Known · saved"
          : action === "hard" ? "Hard · saved"
          : "Saved";
        if (!saved) return;
        item.source = "vault";                 // it is theirs now
        if (action === "known") {
          items.delete(item.word.toLowerCase());   // stop flagging it
          renderHighlights();
        }
        renderRail();
      });
      actions.appendChild(button);
    }
    card.appendChild(actions);
    feedback(item, "opened");
    document.documentElement.appendChild(card);
    close.focus({ preventScroll: true });
  }

  function showError(word, message, point) {
    removeCard();
    const card = element("aside");
    card.id = CARD_ID;
    card.dataset.display = settings.display || "auto";
    card.style.setProperty("--eos-anchor-y", Math.max(12, (point?.y || 86) - 18) + "px");
    card.appendChild(element("div", "eos-reading-kicker", "reading unavailable"));
    card.appendChild(element("h2", "eos-reading-title", word || "EmptyOS"));
    card.appendChild(element("p", "eos-reading-error", message));
    document.documentElement.appendChild(card);
  }

  async function askLookup(word, context, point) {
    showCard({ word, meaning_in_context: "Looking up this word…" }, point, { loading: true });
    const response = await send({ type: "EOS_READING_LOOKUP", word, context },
                                MODEL_TIMEOUT_MS);
    if (!response || response.error) {
      showError(word, response?.error || "The local model did not respond.", point);
      return;
    }
    showCard(response.item, point, { tier: response.source });
  }

  // ── input ───────────────────────────────────────────────────────────────

  // Flow is Ask PLUS proactive flagging, not instead of it. Gating look-up on Ask
  // meant that in Flow the reader could only ask about words the model had already
  // chosen for them — the one mode where they most obviously wanted to point at a
  // word and say "what about THIS one".
  document.addEventListener("dblclick", event => {
    if (mode === "off" || event.target?.closest?.(SKIP)) return;
    const selection = getSelection();
    const word = (selection?.toString() || wordAtPoint(event.clientX, event.clientY)).trim();
    if (!/^[A-Za-z][A-Za-z'-]{1,48}$/.test(word)) return;
    const block = enclosingBlockText(selection?.anchorNode || event.target);
    askLookup(word, sentenceAround(block, word), { x: event.clientX, y: event.clientY });
  }, true);

  document.addEventListener("click", event => {
    if (mode !== "flow" || event.target?.closest?.("#" + CARD_ID + ",#" + RAIL_ID)) return;
    const word = wordAtPoint(event.clientX, event.clientY).toLowerCase();
    const item = items.get(word);
    if (item) showCard(item, { x: event.clientX, y: event.clientY }, { tier: item.source });
  }, true);

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") removeCard();
  }, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "EOS_READING_REFRESH") refresh(message.settings);
    if (message?.type === "EOS_READING_STATE") {
      sendResponse(readingState());
      return true;
    }
  });

  // What the layer is actually holding, for when the rail and the daemon disagree.
  // An empty rail has several very different causes — no scan, a scan in flight, a
  // scan that found nothing, words found but none on this screen, words found but
  // not locatable in the text — and from the outside they look identical. Reading
  // the daemon's cache to guess which one is happening is not debugging.
  //
  // Reachable over a message, not just as a global: a content script lives in an
  // isolated world, so a global here is invisible from the page's own console —
  // which is exactly where someone would think to look for it.
  const readingState = () => ({
    mode,
    scanning,
    scanQueued,
    railError,
    highlightApi: !!(globalThis.CSS && CSS.highlights && globalThis.Highlight),
    visibleTextChars: visibleText().length,
    items: [...items.values()].map(item => ({
      word: item.word,
      source: item.source,
      ranges: (item._ranges || []).length,          // 0 = not found in the page text
      onScreen: (item._ranges || []).some(r => inViewport(r.getBoundingClientRect(), 0)),
    })),
    visible: visibleItems().map(item => item.word),
  });
  window.__EOS_READING_STATE = readingState;   // for the isolated-world console

  async function refresh(next) {
    const reply = next
      ? { settings: next, mode: null }
      : await send({ type: "EOS_READING_SETTINGS", url: location.href });
    if (!reply || reply.error) { applySettings(DEFAULTS, "off"); return; }
    applySettings(reply.settings, reply.mode);
  }

  refresh();
})();
