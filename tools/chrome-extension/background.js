// Background service worker — context menus, badge poller, badge-update fan-in.

importScripts("reading-config.js", "daemon-client.js", "browser-session.js");
const { getConfig, authHeaders } = globalThis.EOS_DAEMON;

globalThis.EOS_BROWSER_SESSION.init().catch(error => {
  globalThis.__EOS_BROWSER_INIT_ERROR__ = error?.message || String(error);
  console.warn("browser session init", error);
});

// ── Config + auth ───────────────────────────────────────────────

// ── Reading settings: the DAEMON owns them, not the browser ───────
// The extension stores only host + token. Every reading preference (mode,
// display, languages, providers, per-site pauses) lives in the dictionary app,
// so /dictionary and the browser can never drift into two different opinions.
// This is a short-lived mirror of that one source, not a second store.
const READING_DEFAULTS = globalThis.EOS_READING_DEFAULTS;
const SETTINGS_TTL_MS = 15000;
let _settingsCache = null;
let _settingsAt = 0;

async function getReadingSettings(force) {
  if (!force && _settingsCache && Date.now() - _settingsAt < SETTINGS_TTL_MS) {
    return _settingsCache;
  }
  try {
    const { host, token } = await getConfig();
    const r = await fetch(host + "/dictionary/api/reading/settings", { headers: authHeaders(token) });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    _settingsCache = { ...READING_DEFAULTS, ...(data.settings || {}) };
  } catch (e) {
    // Daemon unreachable → stay dormant. Failing closed is the only safe default
    // for a layer that would otherwise send page text somewhere.
    _settingsCache = { ...READING_DEFAULTS };
  }
  _settingsAt = Date.now();
  return _settingsCache;
}

function invalidateReadingSettings() {
  _settingsCache = null;
  _settingsAt = 0;
}

function readingHostListed(list, host) {
  return (list || []).some(value => {
    const item = String(value || "").toLowerCase();
    return item && (host === item || host.endsWith("." + item));
  });
}

function readingModeForUrl(settings, url) {
  if (!url || !/^https?:/.test(url)) return "off";
  let host = "";
  try { host = new URL(url).hostname.toLowerCase(); } catch (e) { return "off"; }
  if (readingHostListed(settings.excluded_hosts, host)) return "off";
  // Mail, money, health, messaging: off unless the reader named this host. The
  // page's own gate repeats this check — one of the two failing open would mean
  // reading an inbox and sending it to a model, so neither trusts the other.
  if (globalThis.EOS_READING_IS_PRIVATE_HOST(host)
      && !readingHostListed(settings.allowed_private_hosts, host)) {
    return "off";
  }
  return settings.mode;
}

// ── Side panel + context menu init ──────────────────────────────

// Each verb names the daemon app that answers it. Daemons differ — a public
// release ships neither `dictionary` nor `jobs` — so registering all nine
// unconditionally puts menu items in front of people that can only ever fail.
// `app: ""` marks a verb backed by the runtime itself (always present).
const JOB_SITE_PATTERNS = [
  "https://*.linkedin.com/jobs/*",
  "https://*.seek.com.au/job/*",
];

const MENU_SPECS = [
  { app: "quick-action", spec: { id: "capture-page", title: "Capture page to EmptyOS", contexts: ["page"] } },
  { app: "quick-action", spec: { id: "capture-selection", title: "Capture selection to EmptyOS", contexts: ["selection"] } },
  { app: "quick-action", spec: { id: "capture-link", title: "Capture link to EmptyOS", contexts: ["link"] } },
  { app: "assistant", spec: { id: "propose-kb", title: "Propose selection as KB clause", contexts: ["selection"] } },
  { app: "dictionary", spec: { id: "dict-lookup", title: "Look up '%s' in EmptyOS dictionary", contexts: ["selection"] } },
  { app: "jobs", spec: { id: "eval-job-sel", title: "Evaluate selection as a job (vs my CV)", contexts: ["selection"] } },
  { app: "jobs", spec: { id: "eval-job-page", title: "Evaluate this job posting (vs my CV)", contexts: ["page"], documentUrlPatterns: JOB_SITE_PATTERNS } },
  { app: "jobs", spec: { id: "capture-job", title: "Capture job posting (LinkedIn / Seek)", contexts: ["page"], documentUrlPatterns: JOB_SITE_PATTERNS } },
  { app: "video-digest", spec: { id: "digest-video", title: "Digest this video", contexts: ["page"], documentUrlPatterns: ["https://*.youtube.com/watch*", "https://youtu.be/*"] } },
];

// Serialized: removeAll() racing a concurrent create() throws on duplicate ids.
let _menuBuild = Promise.resolve();

function refreshMenus() {
  _menuBuild = _menuBuild
    .then(_refreshMenus)
    .catch(e => console.warn("contextMenus:", e));
  return _menuBuild;
}

async function _refreshMenus() {
  // null = unknown (daemon down / unconfigured / 401). Unknown is NOT absent:
  // register everything, exactly as before this gating existed. A click on a
  // verb whose app is missing still degrades to an ERR badge.
  const available = await globalThis.EOS_DAEMON.getAvailableApps();
  await chrome.contextMenus.removeAll();
  for (const { app, spec } of MENU_SPECS) {
    if (available && app && !available.has(app)) continue;
    chrome.contextMenus.create(spec);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(e => console.warn("sidePanel:", e));
  refreshMenus();
});

// Browser restart clears storage.session, so the cached probe is gone — re-probe
// rather than falling back to "show everything" for the whole session.
chrome.runtime.onStartup.addListener(() => { refreshMenus(); });

// ── Badge feedback ───────────────────────────────────────────────

async function badge(tabId, text, color) {
  // Verify the tab exists before writing — chrome.action.setBadgeText
  // rejects with "No tab with id: N" when the tab is gone, and although
  // .catch swallows the rejection at the promise level, Chrome still
  // logs the rejection in the extension console. Pre-checking with
  // chrome.tabs.get keeps the console clean.
  if (tabId !== undefined) {
    try { await chrome.tabs.get(tabId); }
    catch (e) { return; }
  }
  const opts = { text: text || "" };
  if (tabId !== undefined) opts.tabId = tabId;
  try { await chrome.action.setBadgeText(opts); } catch (e) { return; }
  if (text) {
    const c = { color: color || "#4a7" };
    if (tabId !== undefined) c.tabId = tabId;
    try { await chrome.action.setBadgeBackgroundColor(c); } catch (e) {}
  }
}

function flashBadge(ok) {
  badge(undefined, ok ? "OK" : "ERR", ok ? "#4a7" : "#d66");
  setTimeout(async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) refreshBadge(tab);
    else chrome.action.setBadgeText({ text: "" });
  }, 1500);
}

async function applyReadingBadge(tab, captured = false) {
  if (!tab?.id) return;
  const settings = await getReadingSettings();
  const mode = readingModeForUrl(settings, tab.url);
  if (mode === "flow") {
    await badge(tab.id, "F", "#596fc4");
    await chrome.action.setTitle({ tabId: tab.id, title: "EmptyOS reading: Flow" + (captured ? " · page captured" : "") });
  } else if (mode === "ask") {
    await badge(tab.id, "A", "#4a7");
    await chrome.action.setTitle({ tabId: tab.id, title: "EmptyOS reading: Ask" + (captured ? " · page captured" : "") });
  } else if (captured) {
    await badge(tab.id, "✓", "#4a7");
    await chrome.action.setTitle({ tabId: tab.id, title: "Open EmptyOS panel · page captured" });
  } else {
    await badge(tab.id, "");
    await chrome.action.setTitle({ tabId: tab.id, title: "Open EmptyOS panel" });
  }
}

// ── Inject content.js into a tab (idempotent) ────────────────────

async function ensureContentLoaded(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });
    return true;
  } catch (e) {
    console.warn("ensureContentLoaded:", e.message);
    return false;
  }
}

async function callSiteExtractor(tabId, name) {
  await ensureContentLoaded(tabId);
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (n) => (window.EOS_SITE && window.EOS_SITE[n]) ? window.EOS_SITE[n]() : null,
      args: [name],
    });
    return res?.result || null;
  } catch (e) {
    console.warn("extractor", name, e.message);
    return null;
  }
}

// ── In-page result card (shared by dict-lookup, job-eval, …) ─────

// Injected into the page to render a result near the selection. Generic
// payload so every selection-verb reuses it (browser-extension-bridge rule):
//   { title, subtitle?, badge?:{text,color}, rows:[{value,tone?}], footer? }
//   row tone ∈ default | muted | zh | quote | gap
// MUST be fully self-contained — the function source is stringified and
// re-evaluated in the target tab, so it can close over nothing but its arg.
// Every interpolated value is escaped before innerHTML.
function renderResultCard(payload) {
  const p = payload || {};
  const ID = "eos-result-card";
  const existing = document.getElementById(ID);
  if (existing) existing.remove();

  let top = window.scrollY + 80, left = window.scrollX + 80;
  const sel = window.getSelection();
  if (sel && sel.rangeCount) {
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    if (rect && (rect.top || rect.left || rect.width)) {
      top = rect.bottom + window.scrollY + 8;
      left = Math.max(window.scrollX + 8,
        Math.min(rect.left + window.scrollX, window.scrollX + window.innerWidth - 360));
    }
  }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const TONE = {
    default: "margin:4px 0",
    muted: "margin:4px 0;color:#9aa0bd",
    zh: "margin:4px 0;color:#bfe3c0",
    quote: "margin:6px 0;padding-left:10px;border-left:2px solid #3a3f66;color:#b9bcd0;font-style:italic",
    gap: "margin:3px 0;color:#e6b980",
  };

  const card = document.createElement("div");
  card.id = ID;
  card.style.cssText = [
    "position:absolute", "top:" + top + "px", "left:" + left + "px",
    "z-index:2147483647", "width:340px", "max-height:62vh", "overflow:auto",
    "box-sizing:border-box", "padding:14px 16px", "border-radius:12px",
    "font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif", "color:#e8eaf2",
    "background:linear-gradient(160deg,#1a1c2e,#12131f)", "border:1px solid #3a3f66",
    "box-shadow:0 8px 32px rgba(0,0,0,.5)",
  ].join(";");

  const out = [];
  out.push(
    '<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:4px">' +
    '<b style="font-size:18px;color:#8ab4ff">' + esc(p.title || "") + "</b>" +
    (p.badge ? '<span style="margin-left:auto;font-weight:700;padding:1px 9px;border-radius:8px;background:' +
      esc(p.badge.color || "#2a2d45") + ';color:#0d0e16">' + esc(p.badge.text || "") + "</span>" : "") +
    "</div>"
  );
  if (p.subtitle) out.push('<div style="color:#9aa0bd;margin-bottom:6px">' + esc(p.subtitle) + "</div>");
  for (const r of (p.rows || [])) {
    if (!r || !r.value) continue;
    out.push('<div style="' + (TONE[r.tone] || TONE.default) + '">' + esc(r.value) + "</div>");
  }
  out.push(
    '<div style="margin-top:10px;display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#8f93ad">' +
    "<span>" + esc(p.footer || "") + "</span>" +
    '<span id="eos-card-close" style="cursor:pointer;padding:2px 9px;border-radius:6px;background:#2a2d45">close</span>' +
    "</div>"
  );
  card.innerHTML = out.join("");
  document.body.appendChild(card);

  const close = () => { card.remove(); document.removeEventListener("mousedown", onDoc); };
  const onDoc = (e) => { if (!card.contains(e.target)) close(); };
  card.querySelector("#eos-card-close").addEventListener("click", close);
  setTimeout(() => document.addEventListener("mousedown", onDoc), 50);
  setTimeout(close, 14000);
}

async function showResultCard(tabId, payload) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: renderResultCard,
      args: [payload],
    });
  } catch (e) { console.warn("showResultCard:", e.message); }
}

// ── Adaptive reading bridge ───────────────────────────────────────────────

// An MV3 service worker is torn down after ~30 seconds idle, and a pending fetch
// does NOT count as activity. A model that thinks for longer than that — claude-cli
// takes ~33s on a full page, a cold local model longer — therefore answers into a
// worker that no longer exists: the daemon does the work, caches the words, and the
// page waits forever on a reply that can never arrive. Only the fastest provider
// appeared to "work", which is what made this look like a model problem.
//
// Any extension API call resets the idle timer. Ticking one while a request is in
// flight is the documented way to survive a slow model.
let _keepAliveTimer = null;
let _readingInFlight = 0;

function keepAliveStart() {
  _readingInFlight += 1;
  if (_keepAliveTimer) return;
  _keepAliveTimer = setInterval(() => {
    chrome.runtime.getPlatformInfo().catch(() => {});
  }, 20000);
}

function keepAliveStop() {
  _readingInFlight = Math.max(0, _readingInFlight - 1);
  if (_readingInFlight === 0 && _keepAliveTimer) {
    clearInterval(_keepAliveTimer);
    _keepAliveTimer = null;
  }
}

async function readingFetch(path, body) {
  keepAliveStart();
  try {
    const { host, token } = await getConfig();
    const response = await fetch(host + path, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await response.json(); }
    catch (e) { data = { error: "EmptyOS returned an unreadable response." }; }
    if (!response.ok && !data.error) data.error = "EmptyOS returned HTTP " + response.status;
    return data;
  } finally {
    keepAliveStop();
  }
}

// Fetch TTS audio with auth and hand it to the page as a data: URL. An <audio
// src> cannot carry an Authorization header, so the bytes have to come through
// here — the page never learns the token.
async function readingPronounce(word) {
  try {
    const { host, token } = await getConfig();
    const r = await fetch(host + "/dictionary/api/pronounce/" + encodeURIComponent(word),
                          { headers: authHeaders(token) });
    if (!r.ok) return { error: "Pronunciation unavailable (HTTP " + r.status + ")" };
    const meta = await r.json();
    if (!meta.audio_url) return { error: meta.error || "No speak provider available." };
    const audio = await fetch(host + meta.audio_url, { headers: authHeaders(token) });
    if (!audio.ok) return { error: "Audio fetch failed (HTTP " + audio.status + ")" };
    const buf = await audio.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const mime = audio.headers.get("Content-Type") || "audio/mpeg";
    return { ok: true, dataUrl: "data:" + mime + ";base64," + btoa(binary) };
  } catch (e) {
    return { error: e?.message || "Pronunciation failed" };
  }
}

async function handleReadingMessage(message, sender) {
  if (message.type === "EOS_READING_SETTINGS") {
    const settings = await getReadingSettings(Boolean(message.force));
    const url = message.url || sender?.tab?.url || "";
    return { ok: true, settings, mode: readingModeForUrl(settings, url) };
  }
  if (message.type === "EOS_READING_KNOWN") {
    // No model, no provider, no cost — the reader's own words, matched in the page.
    return readingFetch("/dictionary/api/reading/known", {
      text: String(message.text || "").slice(0, 8000),
    });
  }
  if (message.type === "EOS_READING_ANALYZE") {
    const s = await getReadingSettings();
    return readingFetch("/dictionary/api/reading/analyze", {
      text: String(message.text || "").slice(0, 8000),
      url: String(message.url || "").slice(0, 1000),
      title: String(message.title || "").slice(0, 300),
      provider: s.flow_provider,
      native: s.native_language,
      target: s.target_language,
    });
  }
  if (message.type === "EOS_READING_LOOKUP") {
    const s = await getReadingSettings();
    return readingFetch("/dictionary/api/reading/lookup", {
      word: String(message.word || "").slice(0, 64),
      context: String(message.context || "").slice(0, 420),
      provider: s.local_provider,
      native: s.native_language,
      target: s.target_language,
    });
  }
  if (message.type === "EOS_READING_FEEDBACK") {
    // The item rides along: "I know this" and "Still hard" are both judgements the
    // reader wants recorded in their vocabulary, and the daemon cannot write a note
    // from a bare word.
    return readingFetch("/dictionary/api/reading/feedback", {
      word: String(message.word || "").slice(0, 64),
      action: String(message.action || "").slice(0, 24),
      item: message.item || null,
      source_url: String(message.sourceUrl || sender?.tab?.url || "").slice(0, 1000),
    });
  }
  if (message.type === "EOS_READING_PRONOUNCE") {
    return readingPronounce(String(message.word || "").slice(0, 64));
  }
  if (message.type === "EOS_READING_SAVE") {
    // Goes through the reading layer's own save, which ENRICHES with a strong model
    // first: the card is thin by design (it has to be fast), but a saved note is
    // read months later and deserves IPA, inflections, every sense, collocations.
    const result = await readingFetch("/dictionary/api/reading/save", {
      item: message.item || {},
      source_url: String(message.sourceUrl || sender?.tab?.url || "").slice(0, 1000),
    });
    return result.error ? result : { ...result, ok: true };
  }
  return { error: "Unknown reading action" };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !String(message.type || "").startsWith("EOS_READING_")) return false;
  handleReadingMessage(message, sender)
    .then(sendResponse)
    .catch(error => sendResponse({ error: error?.message || "Reading request failed" }));
  return true;
});

// ── Job evaluation (POST /jobs/api/discover/evaluate → score card) ──

async function evaluateJob(tab, host, token, jobText, jobMeta) {
  jobText = (jobText || "").trim();
  if (jobText.length < 40) { flashBadge(false); return; }
  badge(undefined, "...", "#c80");
  // Scores the JD against the Master CV configured in Settings (user.cv_path).
  // Uses weighted-HR scoring when [apps.jobs] feature.weighted-hr-scoring.enabled,
  // else legacy scoring — both return match_score + gaps.
  const r = await fetch(host + "/jobs/api/discover/evaluate", {
    method: "POST", headers: authHeaders(token),
    body: JSON.stringify({ text: jobText.slice(0, 6000) }),
  });
  if (!r.ok) { flashBadge(false); return; }
  const ev = await r.json();
  if (ev.error) {
    await showResultCard(tab.id, {
      title: "Job evaluation",
      rows: [{ value: ev.error, tone: "gap" }],
      footer: "set your Master CV in Settings",
    });
    flashBadge(false);
    return;
  }
  const score = Math.round(Number(ev.match_score) || 0);
  const color = score >= 70 ? "#7fce8f" : score >= 45 ? "#e6c86e" : "#e59b9b";
  const rows = [];
  const verdict = ev.verdict || ev.match_level || "";
  if (verdict) rows.push({ value: verdict });
  if (ev.priority) rows.push({ value: "Priority: " + ev.priority, tone: "muted" });
  const gaps = Array.isArray(ev.gaps) ? ev.gaps : [];
  gaps.slice(0, 5).forEach((g) => {
    const t = typeof g === "string" ? g : (g && (g.requirement || g.gap || g.note)) || "";
    if (t) rows.push({ value: "⚠ " + t, tone: "gap" });
  });
  await showResultCard(tab.id, {
    title: ev.role || (jobMeta && jobMeta.role) || "Job match",
    subtitle: ev.company || (jobMeta && jobMeta.company) || "",
    badge: { text: score + "%", color },
    rows,
    footer: ev.scoring_mode === "weighted-hr" ? "weighted-HR · vs your CV" : "evaluated vs your CV",
  });
  flashBadge(true);
}

// ── Context menu handlers ───────────────────────────────────────

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const { host, token } = await getConfig();

  try {
    if (info.menuItemId === "capture-page") {
      const r = await fetch(host + "/quick-action/api/add", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify({ text: tab.title + "\n" + tab.url, tag: "" }),
      });
      flashBadge(r.ok);
      if (r.ok) refreshBadge(tab);
      return;
    }
    if (info.menuItemId === "capture-selection") {
      const text = (info.selectionText || "") + "\n\n" + tab.title + "\n" + tab.url;
      const r = await fetch(host + "/quick-action/api/add", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify({ text, tag: "" }),
      });
      flashBadge(r.ok);
      return;
    }
    if (info.menuItemId === "capture-link") {
      const text = (info.linkText || info.linkUrl) + "\n" + info.linkUrl;
      const r = await fetch(host + "/quick-action/api/add", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify({ text, tag: "" }),
      });
      flashBadge(r.ok);
      return;
    }
    if (info.menuItemId === "propose-kb") {
      const sel = await callSiteExtractor(tab.id, "extractSelectionForKB");
      if (!sel) { flashBadge(false); return; }
      const body = sel.paragraph_context
        ? sel.body + "\n\n> *(context)* " + sel.paragraph_context
        : sel.body;
      const r = await fetch(host + "/assistant/api/propose-kb-note", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify({
          kind: "clause",
          title: sel.title,
          body,
          source: sel.source,
          references: sel.page_title ? [sel.page_title] : [],
        }),
      });
      flashBadge(r.ok);
      return;
    }
    if (info.menuItemId === "capture-job") {
      const isLinkedIn = tab.url.includes("linkedin.com");
      const extractor = isLinkedIn ? "extractJobLinkedIn" : "extractJobSeek";
      const job = await callSiteExtractor(tab.id, extractor);
      if (!job || !job.company) {
        flashBadge(false);
        return;
      }
      // Persist the JD body as a note inside the application (the jobs app
      // accepts a `notes` field). Trim for vault sanity.
      job.notes = job.jd_text ? "## JD\n\n" + job.jd_text : "";
      delete job.jd_text;
      const r = await fetch(host + "/jobs/api/applications/add", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify(job),
      });
      flashBadge(r.ok);
      return;
    }
    if (info.menuItemId === "dict-lookup") {
      const word = (info.selectionText || "").trim().split(/\s+/)[0].replace(/[^\w'-]/g, "");
      if (!word) { flashBadge(false); return; }
      badge(undefined, "...", "#c80");
      // Grab the containing sentence + page URL so the saved word links
      // back to where it was found ("抓词现场·一键回跳"). Best-effort;
      // null-safe if the extractor can't reach the selection.
      const ctx = await callSiteExtractor(tab.id, "extractSelectionSentence");
      // Look up — uses the cached vault copy if it exists, otherwise hits
      // the configured think provider. Either way the response shape is
      // the same.
      const r = await fetch(host + "/dictionary/api/lookup?word=" + encodeURIComponent(word), {
        headers: authHeaders(token),
      });
      if (!r.ok) { flashBadge(false); return; }
      const lookup = await r.json();
      if (lookup.error) { flashBadge(false); return; }
      // Auto-save into the vault dictionary, now carrying the reading
      // context. /api/save accepts the lookup shape plus source_url/sentence.
      const saveBody = {
        word,
        definition: lookup.definition || "",
        phonetic: lookup.phonetic || "",
        part_of_speech: lookup.part_of_speech || "",
        example: lookup.example || "",
        synonyms: lookup.synonyms || [],
        antonyms: lookup.antonyms || [],
        chinese: lookup.chinese || "",
        etymology: lookup.etymology || "",
        usage_notes: lookup.usage_notes || "",
        source_url: (ctx && ctx.source) || tab.url || "",
        sentence: (ctx && ctx.sentence) || "",
      };
      const s = await fetch(host + "/dictionary/api/save", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify(saveBody),
      });
      // Render the definition in-page (划词即查) instead of only flashing a
      // badge — the immersive lookup the badge-only path was missing.
      const dictRows = [];
      if (lookup.definition) dictRows.push({ value: lookup.definition });
      if (lookup.chinese) dictRows.push({ value: lookup.chinese, tone: "zh" });
      if (lookup.example) dictRows.push({ value: lookup.example, tone: "quote" });
      await showResultCard(tab.id, {
        title: word,
        subtitle: [lookup.phonetic, lookup.part_of_speech].filter(Boolean).join(" · "),
        rows: dictRows,
        footer: s.ok ? "✓ saved to vault" : "⚠ save failed",
      });
      flashBadge(s.ok);
      return;
    }
    if (info.menuItemId === "eval-job-sel") {
      // Evaluate whatever the user selected as a job posting.
      await evaluateJob(tab, host, token, info.selectionText || "", null);
      return;
    }
    if (info.menuItemId === "eval-job-page") {
      // Scrape the full JD from a LinkedIn/Seek page, then evaluate it.
      const isLinkedIn = tab.url.includes("linkedin.com");
      const job = await callSiteExtractor(tab.id, isLinkedIn ? "extractJobLinkedIn" : "extractJobSeek");
      if (!job || !job.jd_text) { flashBadge(false); return; }
      await evaluateJob(tab, host, token, job.jd_text, job);
      return;
    }
    if (info.menuItemId === "digest-video") {
      const vid = await callSiteExtractor(tab.id, "extractVideoYouTube");
      if (!vid || !vid.url) { flashBadge(false); return; }
      const r = await fetch(host + "/video-digest/api/queue", {
        method: "POST", headers: authHeaders(token),
        body: JSON.stringify({ url: vid.url, title: vid.title || tab.title }),
      });
      flashBadge(r.ok);
      return;
    }
  } catch (e) {
    flashBadge(false);
  }
});

// ── "Already captured" badge poller ─────────────────────────────

const badgeDebounce = new Map();   // tabId -> timeout id

async function refreshBadge(tab) {
  if (!tab || !tab.url || !/^https?:/.test(tab.url)) {
    badge(tab?.id, "");
    return;
  }
  try {
    const { host, token } = await getConfig();
    const r = await fetch(host + "/quick-action/api/has?url=" + encodeURIComponent(tab.url), {
      headers: authHeaders(token),
    });
    if (!r.ok) { applyReadingBadge(tab); return; }
    const data = await r.json();
    // Re-verify the tab still exists before writing the badge — the user
    // may have closed it during the round-trip. chrome.tabs.get throws
    // when the tab is gone; we swallow that case.
    try { await chrome.tabs.get(tab.id); } catch (e) { return; }
    applyReadingBadge(tab, Boolean(data.has));
  } catch (e) {
    applyReadingBadge(tab);
  }
}

function scheduleBadge(tab) {
  if (!tab?.id) return;
  const prev = badgeDebounce.get(tab.id);
  if (prev) clearTimeout(prev);
  badgeDebounce.set(tab.id, setTimeout(() => {
    badgeDebounce.delete(tab.id);
    refreshBadge(tab);
  }, 800));
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" || changeInfo.url) scheduleBadge(tab);
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    scheduleBadge(tab);
  } catch (e) { /* tab gone */ }
});

// The side panel writes settings to the daemon, then pings us. We re-read the one
// source of truth and push it to every open tab — there is no second store to sync.
async function broadcastReadingSettings() {
  const settings = await getReadingSettings(true);
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!/^https?:/.test(tab.url || "")) continue;
    chrome.tabs.sendMessage(tab.id, { type: "EOS_READING_REFRESH", settings }).catch(() => {});
    applyReadingBadge(tab);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "EOS_READING_SETTINGS_CHANGED") return false;
  invalidateReadingSettings();
  broadcastReadingSettings()
    .then(() => sendResponse({ ok: true }))
    .catch(e => sendResponse({ error: e?.message || "broadcast failed" }));
  return true;
});

// Pointing the extension at a different daemon changes which verbs can work.
// Separate from the reading-sync ping so a failure in one can't take the other
// down — they answer different questions about the same save.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "EOS_APPS_CHANGED") return false;
  refreshMenus()
    .then(() => sendResponse({ ok: true }))
    .catch(e => sendResponse({ error: e?.message || "menu refresh failed" }));
  return true;
});
