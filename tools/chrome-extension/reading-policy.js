// The reading layer's DECISIONS, with nothing in them that touches the world.
//
// No DOM, no chrome.*, no fetch, no timers, no clock. Inputs in, verdict out. The
// point is not tidiness: every one of these rules had a real bug in it, and none
// could be tested. `reading-assist.js` still owns the state and everything that
// touches the page — it just no longer decides what the state MEANS.
//
// Four bugs shipped from this file's contents while they were inline (2026-07-14):
//   * a page that mutates forever starved the scan forever (no debounce ceiling)
//   * a panel tweak discarded an answer the model had already been paid for
//   * the rail announced "nothing flagged" on a scan it had never run
//   * a menu was read as prose, and prose inside a link was thrown away
// Each is now a few lines below, and a few lines of test.
//
// Loaded BEFORE reading-assist.js (see syncReadingScripts in browser-session.js).
// Requireable from node, which is how it is tested.

(() => {
  // How often one view may be paid for. A floor, not a deadline.
  const MIN_SCAN_INTERVAL = 20000;
  // How long fresh page activity may push a pending scan back. A real page NEVER
  // stops mutating — an ad slot, a React re-render, a ticking clock — and each
  // mutation re-arms the debounce, so without a ceiling the scan is perpetually a
  // moment away and never runs at all.
  const MAX_SCAN_DEFER = 3000;
  // A ceiling on a silent hang, not a target. A cold local model can be slow.
  const MODEL_TIMEOUT_MS = 150000;
  const DEFAULT_SETTLE_MS = 900;
  // Below this there is nothing worth sending, and nothing worth reporting.
  const MIN_TEXT_CHARS = 80;

  // `a` is deliberately NOT here: a link inside a sentence is prose, often the most
  // interesting word on the page. Menus are excluded by SHAPE instead — see
  // linkIsProse — because half the web builds navigation out of <ul><li><a> and
  // never mentions <nav>.
  const SKIP = "script,style,noscript,textarea,input,select,option,button,pre,code," +
    "nav,header,footer,aside,menu," +
    "#eos-reading-card,#eos-reading-rail,[contenteditable='true'],[contenteditable='']";
  const PROSE = "p,li,blockquote,td,dd,figcaption,h1,h2,h3,h4,h5,h6,article,section,main";

  // Changing one of these invalidates the words on screen — a different model or a
  // different language means different answers. Everything else in the panel (rail,
  // pronounce, display, the Ask-tier model) only changes how the SAME words are shown.
  const RESCAN_KEYS = ["flow_provider", "native_language", "target_language"];

  const RAIL_TEXT = {
    reading: "Reading this screen…",
    "none-here": "Nothing to flag on this screen.",
    "too-little": "Not enough text on this screen to read. Scroll to the article.",
    empty: "Nothing flagged yet.",
  };

  // What a word's SOURCE entitles the UI to claim, and — the part that was wrong —
  // whether it is in the reader's dictionary. Only `vault` means that. `cache` means
  // an answer was remembered, which is a fact about OUR cost and nothing about the
  // reader's vocabulary; it labelled itself "saved" and so a word the reader had
  // never saved sat under a chip reading "saved" with a Save button beside it, which
  // reads as a broken button rather than as an unsaved word (2026-08-17).
  // Only `vault` speaks about the READER; the other two speak about the ANSWER, and
  // keeping that line clean is the whole rule. "seen" was tried and rejected for
  // crossing it — seen by whom? It reads as "I have met this word", which is a claim
  // about the reader's vocabulary and so the same category error in a new word.
  const WORD_SOURCE = {
    vault: { chip: "yours", title: "From your own saved note", saved: true },
    cache: { chip: "cached", title: "A remembered explanation — no model call", saved: false },
    model: { chip: "new", title: "Freshly generated", saved: false },
  };

  // ── when to scan ────────────────────────────────────────────────────────

  // Settling is worth waiting for; waiting forever is not. Past MAX_SCAN_DEFER from
  // the moment a scan was FIRST queued, new activity stops being allowed to push it
  // back. The throttle is a separate floor: it stops us paying twice for one view,
  // and firing early would only re-scan a region we already hold.
  //
  // `queuedAt` returns to the caller so the caller can store it: the ceiling is
  // measured from the first queue, not the latest one, which is the whole fix.
  function scanDelay({ now, lastScanAt = 0, queuedAt = 0, wasQueued = false, delay }) {
    const firstQueuedAt = wasQueued ? queuedAt : now;
    const settle = Math.min(
      delay === undefined ? DEFAULT_SETTLE_MS : delay,
      Math.max(0, firstQueuedAt + MAX_SCAN_DEFER - now),
    );
    // lastScanAt 0 means NEVER SCANNED — nothing has been paid for, so nothing is
    // owed. Stating that is not pedantry: the old code relied on `now - 0` being
    // enormous because Date.now() is, so the sentinel worked by accident and would
    // have silently stalled the first scan for 20s under any other clock.
    const throttle = lastScanAt
      ? Math.max(0, MIN_SCAN_INTERVAL - (now - lastScanAt))
      : 0;
    return { wait: Math.max(settle, throttle), queuedAt: firstQueuedAt };
  }

  // An answer a newer scan has superseded, or that arrived after the reader left
  // Flow. The model call was still worth making; this reply is simply no longer the
  // one the rail is waiting for.
  function acceptResponse({ serial, currentSerial, mode }) {
    return serial === currentSerial && mode === "flow";
  }

  function hasEnoughText(text) {
    return String(text || "").length >= MIN_TEXT_CHARS;
  }

  // ── what the rail may claim ─────────────────────────────────────────────

  // The load-bearing rule: NEVER report a verdict we did not seek. An empty rail has
  // several very different causes and they look identical from outside — a scan that
  // is still coming, a scan that found nothing, a screen we never sent, a screen
  // whose words are all out of view. Saying "nothing flagged" for any of the others
  // is a lie, and it is the lie that made a working pipeline look broken.
  function railState({ visibleCount = 0, itemCount = 0, scanning = false,
                       scanQueued = false, tooLittleText = false, railError = "" }) {
    if (visibleCount > 0) return { kind: "rows", message: "" };
    if (railError) return { kind: "error", message: railError };
    if (scanning || scanQueued) return { kind: "reading", message: RAIL_TEXT.reading };
    if (itemCount > 0) return { kind: "none-here", message: RAIL_TEXT["none-here"] };
    if (tooLittleText) return { kind: "too-little", message: RAIL_TEXT["too-little"] };
    return { kind: "empty", message: RAIL_TEXT.empty };
  }

  // ── what a word's source means ──────────────────────────────────────────

  function wordSource(source) {
    return WORD_SOURCE[String(source || "")] || WORD_SOURCE.model;
  }

  // A verdict click writes two things and they can disagree: the judgement always
  // lands in the profile (`ok`), while the vault note is a separate write that can
  // fail (`saved`). Reporting the first as if it were the second told the reader
  // "Known · saved" over a note that was never written. Save is the one action whose
  // whole point IS the note, so there `ok` and `saved` are the same claim.
  //
  // Both flags come back because both have consequences: `judged` is what stops the
  // word being flagged again, `saved` is what makes it the reader's.
  //
  // `rating` is the third answer, and it is three-valued on purpose. A verdict
  // ROUTES a word; only the star row rates it, so the daemon reports the number
  // the note already holds and OMITS the key when it could not read the note.
  // `null` therefore means "not told", which is not `0` ("the reader looked and
  // said it is fine") — repainting a 3-star row to empty on a non-answer looks
  // exactly like the click having eaten the rating, and the note still holds the
  // 3, so it returns on the next open. `0` is a real value and must survive.
  function saveVerdict({ action, result }) {
    const reply = result || {};
    const ok = Boolean(reply.ok) && !reply.error;
    // `Number.isFinite`, not `typeof === "number"`: NaN and Infinity pass the
    // typeof check and would paint through `Number(x) || 0` as a definite 0.
    // It coerces nothing, so a string "3" is still refused.
    const rating = Number.isFinite(reply.difficulty) ? reply.difficulty : null;
    if (action === "save") {
      return { judged: ok, saved: ok, rating, label: ok ? "Saved" : "Save failed" };
    }
    const word = action === "known" ? "Known" : "Hard";
    if (!ok) return { judged: false, saved: false, rating: null, label: "Failed" };
    if (!reply.saved) return { judged: true, saved: false, rating, label: word + " · not saved" };
    return { judged: true, saved: true, rating, label: word + " · saved" };
  }

  // ── what a settings change means ────────────────────────────────────────

  // Every control in the panel saves, and every save broadcasts to the page. A full
  // teardown on each one discarded the scan in flight — the model call was made and
  // paid for, the answer arrived, and it was thrown away because the serial had
  // moved. Tear down for a change that invalidates the words; a cosmetic one just
  // re-renders them.
  function shouldRestart({ before = {}, after = {}, beforeMode, wantedMode }) {
    if (wantedMode !== beforeMode) return true;
    return RESCAN_KEYS.some(key => before[key] !== after[key]);
  }

  function hostListed(list, host) {
    const here = String(host || "").toLowerCase();
    return (list || []).some(value => {
      const entry = String(value || "").toLowerCase();
      return entry && (here === entry || here.endsWith("." + entry));
    });
  }

  // A per-site pause must survive a broadcast refresh, which carries settings but no
  // per-tab mode — so the exclusion is applied HERE rather than trusted from the
  // message. Mail, money, health and messaging hosts are dormant until the reader
  // names the host: reading them would send private correspondence to a model.
  function effectiveMode({ settings = {}, nextMode = null, hostname = "",
                           isPrivateHost = null }) {
    const wanted = nextMode || settings.mode || "off";
    if (hostListed(settings.excluded_hosts, hostname)) return "off";
    const privateCheck = isPrivateHost
      || (typeof globalThis.EOS_READING_IS_PRIVATE_HOST === "function"
          ? globalThis.EOS_READING_IS_PRIVATE_HOST : null);
    const isPrivate = privateCheck ? Boolean(privateCheck(hostname)) : false;
    if (isPrivate && !hostListed(settings.allowed_private_hosts, hostname)) return "off";
    return wanted;
  }

  // ── what counts as prose ────────────────────────────────────────────────

  // A menu item IS its link: the link text is the whole block. A prose link is a few
  // words inside a sentence that goes on without it. The caller does the DOM walk
  // and hands over the two lengths.
  function linkIsProse({ blockLen = 0, linkLen = 0 }) {
    return blockLen > MIN_TEXT_CHARS && blockLen >= linkLen * 3;
  }

  // djb2 — identifies a region so we never pay twice for the same view.
  function fingerprint(text) {
    let h = 5381;
    const value = String(text || "");
    for (let i = 0; i < value.length; i++) h = ((h << 5) + h + value.charCodeAt(i)) | 0;
    return String(h);
  }

  const POLICY = {
    MIN_SCAN_INTERVAL, MAX_SCAN_DEFER, MODEL_TIMEOUT_MS, MIN_TEXT_CHARS,
    SKIP, PROSE, RESCAN_KEYS, RAIL_TEXT,
    scanDelay, acceptResponse, hasEnoughText, railState, shouldRestart,
    wordSource, saveVerdict, effectiveMode, linkIsProse, fingerprint,
  };

  globalThis.EOS_READING_POLICY = POLICY;
  // Requireable from node — this is how the rules above are actually tested.
  if (typeof module !== "undefined" && module.exports) module.exports = POLICY;
})();
