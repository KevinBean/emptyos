// Lecture capture: record a tab's audio, hand it to the daemon, get a vault note.
//
// Exists because paid course video is commonly a referer-locked cross-origin
// embed with no caption track — the spoken content can't be downloaded or read.
// Tab capture is the one route that works, and it is scoped to a single tab, so
// unrelated system audio can never leak in (a full-desktop capture script has no
// such guarantee — the first one written here caught a Gmail page).
//
// All work rides the local daemon: this file records and uploads, and
// /academy/api/lecture/capture does the transcription and the vault write.

(() => {
  const OFFSCREEN_PATH = 'offscreen.html';
  // Chrome caps a single message at ~64MB and a data: URL costs ~33% over raw.
  // Opus tab audio is roughly 0.5 MB/min, so this is ~90 min of lecture.
  const MAX_BYTES = 45 * 1024 * 1024;

  async function hasOffscreen() {
    if (!chrome.runtime.getContexts) return false;
    const c = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    return (c || []).length > 0;
  }

  async function ensureOffscreen() {
    if (await hasOffscreen()) return;
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_PATH,
        reasons: ['USER_MEDIA'],
        justification: 'Record lecture audio from the active tab for transcription.',
      });
    } catch (e) {
      // A concurrent create wins the race; anything else is real.
      if (!/single offscreen|already exists/i.test(e?.message || '')) throw e;
    }
  }

  function toOffscreen(msg) {
    return chrome.runtime.sendMessage({ ...msg, target: 'eos-offscreen' });
  }

  // authHeaders() sets Content-Type: application/json, because every other
  // caller in this extension sends JSON. Sending that with a FormData body stops
  // fetch generating the multipart boundary, so the server parses an empty form
  // and reports "no file" — which cost an hour of chasing a phantom bug in
  // working code. Both uploads go through here so it can only be got wrong once.
  async function postForm(path, fd) {
    const { host, token } = await EOS_DAEMON.getConfig();
    const headers = { ...EOS_DAEMON.authHeaders(token) };
    delete headers['Content-Type'];
    let res;
    try {
      res = await fetch(host + path, { method: 'POST', headers, body: fd });
    } catch (e) {
      return { error: 'could not reach the daemon (' + (e?.message || 'network') + ')' };
    }
    if (!res.ok) return { error: 'daemon returned HTTP ' + res.status };
    const data = await res.json().catch(() => ({}));
    if (!data.ok) return { error: data.error || 'daemon could not file it' };
    return data;
  }

  async function state() {
    if (!(await hasOffscreen())) return { recording: false, seconds: 0 };
    try {
      const r = await toOffscreen({ type: 'LECTURE_REC_STATE' });
      return { recording: !!r?.recording, seconds: r?.seconds || 0 };
    } catch (e) {
      return { recording: false, seconds: 0 };
    }
  }

  async function start({ tabId }) {
    const cur = await state();
    if (cur.recording) return { error: 'already recording' };

    let tab;
    try {
      tab = tabId ? await chrome.tabs.get(tabId)
                  : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    } catch (e) {
      return { error: 'no tab to capture' };
    }
    if (!tab?.id) return { error: 'no tab to capture' };
    if (!/^https?:/.test(tab.url || '')) {
      return { error: 'that tab is not a web page' };
    }

    let streamId;
    try {
      streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    } catch (e) {
      // Most often: no user gesture, or the tab was never "invoked" for this
      // extension. Say so rather than surfacing a bare Chrome string.
      return { error: 'could not start tab capture (' + (e?.message || 'unknown') +
                      ') — click Start again with the lecture tab focused' };
    }

    await ensureOffscreen();
    const r = await toOffscreen({ type: 'LECTURE_REC_START', streamId });
    if (!r?.ok) {
      // Don't leave an idle offscreen document behind: hasOffscreen() is what
      // Stop uses to decide a capture is running, so a leaked one makes Stop
      // report a recorder failure instead of "not recording".
      try { await chrome.offscreen.closeDocument(); } catch (e) { /* already gone */ }
      return { error: r?.error || 'recorder refused to start' };
    }

    await chrome.storage.session.set({
      lectureCapture: { tabId: tab.id, title: tab.title || '', url: tab.url || '', startedAt: Date.now() },
    });
    return { ok: true, tabId: tab.id, title: tab.title || '', mimeType: r.mimeType };
  }

  async function stop({ label, folder, course } = {}) {
    if (!(await hasOffscreen())) return { error: 'not recording' };
    const r = await toOffscreen({ type: 'LECTURE_REC_STOP' });
    const { lectureCapture } = await chrome.storage.session.get({ lectureCapture: null });
    await chrome.storage.session.remove('lectureCapture');
    try { await chrome.offscreen.closeDocument(); } catch (e) { /* already gone */ }

    if (!r?.ok) return { error: r?.error || 'recorder failed' };
    if (r.bytes > MAX_BYTES) {
      return { error: 'recording too long to upload (' +
                      (r.bytes / 1048576).toFixed(0) + ' MB) — capture shorter segments' };
    }

    const blob = await (await fetch(r.dataUrl)).blob();
    const ext = /ogg/.test(r.mimeType) ? 'ogg' : 'webm';
    const fd = new FormData();
    fd.append('audio', blob, 'lecture.' + ext);
    fd.append('label', label || 'lecture');
    if (folder) fd.append('folder', folder);
    if (course) fd.append('course', course);
    fd.append('title', lectureCapture?.title || '');
    fd.append('url', lectureCapture?.url || '');

    const data = await postForm('/academy/api/lecture/capture', fd);
    if (data.error) return data;
    return { ok: true, seconds: Math.round(r.seconds), bytes: r.bytes, ...data };
  }

  async function abort() {
    if (await hasOffscreen()) {
      try { await toOffscreen({ type: 'LECTURE_REC_ABORT' }); } catch (e) { /* gone */ }
      try { await chrome.offscreen.closeDocument(); } catch (e) { /* gone */ }
    }
    await chrome.storage.session.remove('lectureCapture');
    return { ok: true };
  }

  // The context menu is the reliable way in: clicking a menu item IS an
  // extension invocation, so it grants activeTab for that tab. A click on a
  // button inside the side panel does not, which is why Start alone can fail
  // with "Extension has not been invoked for the current page".
  globalThis.EOS_LECTURE = { start, stop, state, abort };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    const t = message?.type;
    if (t !== 'EOS_LECTURE_START' && t !== 'EOS_LECTURE_STOP' &&
        t !== 'EOS_LECTURE_STATE' && t !== 'EOS_LECTURE_ABORT') return false;
    const run = async () => {
      if (t === 'EOS_LECTURE_START') return await start(message);
      if (t === 'EOS_LECTURE_STOP') return await stop(message);
      if (t === 'EOS_LECTURE_ABORT') return await abort();
      return await state();
    };
    run().then(sendResponse).catch(e => sendResponse({ error: e?.message || String(e) }));
    return true;
  });
  // ── Page capture ─────────────────────────────────────────────────────
  // Screenshot the visible tab plus the page's own text, and append both to the
  // lecture note. Separate from audio capture on purpose: audio is a session
  // with a start and a stop, this is a one-shot verb fired on the slide wanted.
  //
  // Same scope as the audio path, not a second IIFE, so both can share
  // postForm() — the multipart trap above is worth having exactly one copy of.
  //
  // captureVisibleTab needs activeTab or host permission for the tab, so it
  // rides the same invocation paths as the audio start: menu or shortcut.
  const MAX_TEXT = 20000;

  async function pageText(tabId) {
    try {
      const [{ result } = {}] = await chrome.scripting.executeScript({
        target: { tabId },
        func: (cap) => {
          // innerText, not textContent: it respects layout, so it skips hidden
          // nodes and keeps the line breaks that make a slide readable.
          const t = (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
          return t.slice(0, cap);
        },
        args: [MAX_TEXT],
      });
      return result || '';
    } catch (e) {
      return '';   // no host permission for this tab; the screenshot may still work
    }
  }

  async function capturePage({ tabId, label, folder } = {}) {
    let tab;
    try {
      tab = tabId ? await chrome.tabs.get(tabId)
                  : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    } catch (e) { return { error: 'no tab to capture' }; }
    if (!tab?.id) return { error: 'no tab to capture' };
    if (!/^https?:/.test(tab.url || '')) return { error: 'that tab is not a web page' };

    let dataUrl = '';
    try {
      dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 80 });
    } catch (e) {
      return { error: 'could not screenshot (' + (e?.message || 'unknown') +
                      ') — start it from the page: right-click, or the shortcut' };
    }

    const text = await pageText(tab.id);
    const store = await chrome.storage.local.get({ lectureLabel: '', lectureFolder: '' });
    const lbl = (label || store.lectureLabel || '').trim() ||
      (tab.title || 'lecture').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 48);

    const blob = await (await fetch(dataUrl)).blob();
    const fd = new FormData();
    fd.append('image', blob, 'shot.jpg');
    if (text) fd.append('text', text);
    fd.append('label', lbl);
    fd.append('folder', (folder || store.lectureFolder || '').trim());
    fd.append('title', tab.title || '');
    fd.append('url', tab.url || '');

    const data = await postForm('/academy/api/lecture/page', fd);
    if (data.error) return data;
    return { ok: true, textChars: text.length, ...data };
  }

  globalThis.EOS_LECTURE_PAGE = { capturePage };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== 'EOS_LECTURE_PAGE') return false;
    capturePage(message).then(sendResponse)
      .catch(e => sendResponse({ error: e?.message || String(e) }));
    return true;
  });
})();
