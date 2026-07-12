// Background service worker — context menus, badge poller, badge-update fan-in.

// ── Config + auth ───────────────────────────────────────────────

async function getConfig() {
  const { host, token } = await chrome.storage.sync.get({
    host: "http://localhost:9000",
    token: "",
  });
  return { host: host.replace(/\/$/, ""), token };
}

function authHeaders(token) {
  const h = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = "Bearer " + token;
  return h;
}

// ── Side panel + context menu init ──────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(e => console.warn("sidePanel:", e));

  chrome.contextMenus.create({
    id: "capture-page",
    title: "Capture page to EmptyOS",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "capture-selection",
    title: "Capture selection to EmptyOS",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "capture-link",
    title: "Capture link to EmptyOS",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: "propose-kb",
    title: "Propose selection as KB clause",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "dict-lookup",
    title: "Look up '%s' in EmptyOS dictionary",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "eval-job-sel",
    title: "Evaluate selection as a job (vs my CV)",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "eval-job-page",
    title: "Evaluate this job posting (vs my CV)",
    contexts: ["page"],
    documentUrlPatterns: [
      "https://*.linkedin.com/jobs/*",
      "https://*.seek.com.au/job/*",
    ],
  });
  chrome.contextMenus.create({
    id: "capture-job",
    title: "Capture job posting (LinkedIn / Seek)",
    contexts: ["page"],
    documentUrlPatterns: [
      "https://*.linkedin.com/jobs/*",
      "https://*.seek.com.au/job/*",
    ],
  });
  chrome.contextMenus.create({
    id: "digest-video",
    title: "Digest this video",
    contexts: ["page"],
    documentUrlPatterns: [
      "https://*.youtube.com/watch*",
      "https://youtu.be/*",
    ],
  });
});

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
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 1500);
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
    if (!r.ok) { badge(tab.id, ""); return; }
    const data = await r.json();
    // Re-verify the tab still exists before writing the badge — the user
    // may have closed it during the round-trip. chrome.tabs.get throws
    // when the tab is gone; we swallow that case.
    try { await chrome.tabs.get(tab.id); } catch (e) { return; }
    if (data.has) {
      badge(tab.id, "✓", "#4a7");
    } else {
      badge(tab.id, "");
    }
  } catch (e) {
    badge(tab.id, "");
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
