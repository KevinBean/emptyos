// Records one tab's audio for a lecture capture.
//
// Two things here are load-bearing and easy to get wrong:
//
// 1. Capturing a tab's audio SILENCES the tab for the user. The stream is
//    routed back to the speakers through an AudioContext so the lecture is
//    still audible while it records. Without this the user hears nothing and
//    reasonably concludes the feature is broken.
// 2. This runs in an offscreen document, not the service worker, because MV3
//    tears the worker down after ~30s idle and a lecture runs for minutes.
//
// The finished audio goes back to the background worker as a data: URL —
// structured-clone can't carry a Blob across the extension message boundary.

(() => {
  let recorder = null;
  let chunks = [];
  let stream = null;
  let audioCtx = null;
  let startedAt = 0;

  function pickMime() {
    const prefs = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    for (const m of prefs) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
    }
    return '';
  }

  async function start(streamId) {
    if (recorder) throw new Error('already recording');
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
      video: false,
    });

    // Keep the tab audible while we capture it.
    audioCtx = new AudioContext();
    audioCtx.createMediaStreamSource(stream).connect(audioCtx.destination);

    chunks = [];
    const mimeType = pickMime();
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    // A timeslice means a crash mid-lecture still leaves usable audio behind
    // rather than one unwritten blob.
    recorder.start(2000);
    startedAt = Date.now();
    return { mimeType: recorder.mimeType || mimeType || 'audio/webm' };
  }

  function teardown() {
    try { stream?.getTracks().forEach(t => t.stop()); } catch (e) { /* already gone */ }
    try { audioCtx?.close(); } catch (e) { /* already closed */ }
    stream = null;
    audioCtx = null;
  }

  async function stop() {
    if (!recorder) throw new Error('not recording');
    const rec = recorder;
    const mimeType = rec.mimeType || 'audio/webm';
    const done = new Promise(res => { rec.onstop = res; });
    rec.stop();
    await done;
    recorder = null;
    const seconds = (Date.now() - startedAt) / 1000;
    const blob = new Blob(chunks, { type: mimeType });
    chunks = [];
    teardown();
    if (!blob.size) throw new Error('captured 0 bytes — was the tab silent?');
    const dataUrl = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(new Error('could not encode audio'));
      fr.readAsDataURL(blob);
    });
    return { dataUrl, bytes: blob.size, seconds, mimeType };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.target !== 'eos-offscreen') return false;
    const run = async () => {
      if (msg.type === 'LECTURE_REC_START') return await start(msg.streamId);
      if (msg.type === 'LECTURE_REC_STOP') return await stop();
      if (msg.type === 'LECTURE_REC_STATE') {
        return { recording: !!recorder, seconds: recorder ? (Date.now() - startedAt) / 1000 : 0 };
      }
      if (msg.type === 'LECTURE_REC_ABORT') {
        try { recorder?.stop(); } catch (e) { /* nothing to stop */ }
        recorder = null; chunks = []; teardown();
        return { aborted: true };
      }
      throw new Error('unknown offscreen message: ' + msg.type);
    };
    run().then(r => sendResponse({ ok: true, ...r }))
         .catch(e => sendResponse({ ok: false, error: e?.message || String(e) }));
    return true;
  });
})();
