// EmptyOS site extractors. Two roles:
//   1. detectSite(url) — classifies the current tab; used to surface
//      "Capture as job / Digest video" chips in the side panel.
//   2. extract*() — pure functions injected into a tab via
//      chrome.scripting.executeScript({func}). They must NOT close over
//      anything outside themselves — the function source is stringified
//      and re-evaluated in the target tab's context.

window.EOS_SITE = {
  detectSite(url) {
    if (!url) return null;
    try {
      const u = new URL(url);
      if (u.hostname.includes("linkedin.com") && u.pathname.startsWith("/jobs/")) {
        return { kind: "job", source: "linkedin", url };
      }
      if (u.hostname.includes("seek.com") && /\/job\//.test(u.pathname)) {
        return { kind: "job", source: "seek", url };
      }
      if (u.hostname.includes("youtube.com") && u.pathname === "/watch") {
        return { kind: "video", source: "youtube", url };
      }
      if (u.hostname.includes("youtu.be")) {
        return { kind: "video", source: "youtube", url };
      }
    } catch (e) { /* malformed url */ }
    return null;
  },

  // ── Injectable scrapers (must be self-contained) ───────────────

  extractJobLinkedIn() {
    function text(sel) {
      const el = document.querySelector(sel);
      return el ? el.textContent.trim().replace(/\s+/g, " ") : "";
    }
    const role = text("h1.t-24, h1[class*='top-card-layout__title'], .job-details-jobs-unified-top-card__job-title h1");
    const company = text(".job-details-jobs-unified-top-card__company-name a, .topcard__org-name-link, [data-tracking-control-name='public_jobs_topcard-org-name']");
    const loc = text(".job-details-jobs-unified-top-card__bullet, .topcard__flavor--bullet, .job-details-jobs-unified-top-card__primary-description-without-tagline");
    const salary = text(".job-details-jobs-unified-top-card__job-insight, .salary, [class*='salary']");
    const desc = document.querySelector(".jobs-description__content, .description__text, #job-details");
    const jd_text = desc ? desc.innerText.trim().slice(0, 6000) : "";
    return {
      company: company || "",
      role: role || "",
      location: loc || "",
      salary: salary || "",
      source: "linkedin",
      url: window.location.href,
      jd_text,
      notes: "",
    };
  },

  extractJobSeek() {
    function text(sel) {
      const el = document.querySelector(sel);
      return el ? el.textContent.trim().replace(/\s+/g, " ") : "";
    }
    const role = text("[data-automation='job-detail-title'], h1");
    const company = text("[data-automation='advertiser-name'], [data-automation='job-detail-company-name']");
    const location = text("[data-automation='job-detail-location']");
    const salary = text("[data-automation='job-detail-salary']");
    const desc = document.querySelector("[data-automation='jobAdDetails']");
    const jd_text = desc ? desc.innerText.trim().slice(0, 6000) : "";
    return {
      company: company || "",
      role: role || "",
      location: location || "",
      salary: salary || "",
      source: "seek",
      url: window.location.href,
      jd_text,
      notes: "",
    };
  },

  extractVideoYouTube() {
    function text(sel) {
      const el = document.querySelector(sel);
      return el ? el.textContent.trim().replace(/\s+/g, " ") : "";
    }
    const title = document.title.replace(/ - YouTube$/, "");
    const channel = text("#owner #channel-name a, ytd-channel-name a");
    const url = window.location.href;
    return { url, title, channel };
  },

  // For selection → KB clause flow. The extension's right-click context
  // menu fires this. Returns { title, body, source, paragraph_context }.
  extractSelectionForKB() {
    const sel = window.getSelection();
    if (!sel || !sel.toString().trim()) return null;
    const text = sel.toString().trim();
    // Climb to the nearest <p> or section for paragraph context. The
    // climb can reach Document (no tagName, .textContent === null) — guard
    // every access so we don't crash on edge cases.
    let node = sel.anchorNode;
    while (node && node.nodeType === 3) node = node.parentNode;
    let para = node;
    let hops = 0;
    while (para && para.tagName &&
           !["P", "LI", "BLOCKQUOTE", "ARTICLE", "SECTION"].includes(para.tagName) &&
           hops++ < 30) {
      para = para.parentNode;
    }
    const paraText = (para && para.textContent) ? para.textContent : "";
    const paragraph = paraText.trim().slice(0, 1500);
    const title = text.slice(0, 80).replace(/[\n\r]+/g, " ");
    return {
      title,
      body: text,
      source: window.location.href,
      paragraph_context: paragraph && paragraph !== text ? paragraph : "",
      page_title: document.title,
    };
  },

  // For the dictionary lookup flow: return the selected text, the single
  // sentence containing it, and the source URL — so a saved word links
  // back to where it was found ("抓词现场·一键回跳"). Best-effort; the
  // caller tolerates null.
  extractSelectionSentence() {
    const sel = window.getSelection();
    if (!sel || !sel.toString().trim()) return null;
    const text = sel.toString().trim();
    // Climb to the nearest block element for context (same boundary set as
    // extractSelectionForKB; guard every access — the climb can reach
    // Document, which has no tagName).
    let node = sel.anchorNode;
    while (node && node.nodeType === 3) node = node.parentNode;
    let para = node;
    let hops = 0;
    while (para && para.tagName &&
           !["P", "LI", "BLOCKQUOTE", "ARTICLE", "SECTION"].includes(para.tagName) &&
           hops++ < 30) {
      para = para.parentNode;
    }
    const paraText = ((para && para.textContent) ? para.textContent : "")
      .replace(/\s+/g, " ").trim();
    // Narrow the paragraph to the one sentence containing the selection.
    let sentence = "";
    if (paraText) {
      const parts = paraText.split(/(?<=[.!?。！？])\s+/);
      sentence = (parts.find(s => s.includes(text)) || paraText).trim().slice(0, 400);
    }
    return {
      selection: text,
      sentence,
      source: window.location.href,
      page_title: document.title,
    };
  },
};
