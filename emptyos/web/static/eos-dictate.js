/**
 * EmptyOS Dictation Overlay
 *
 * Loaded globally by eos.js (only when the `dictation` app is enabled). Two
 * triggers, both insert the polished transcript at the caret:
 *   - a floating mic button that anchors to the focused text field (click to
 *     start, click again to stop) — the no-hotkey path, conflict-free;
 *   - a push-to-talk hotkey: hold the configured chord inside any text field.
 * Speak to create, type to refine — never steals the keyboard.
 *
 * The genuinely-new primitive here is focused-field insertion
 * (`EOS.dictate.insertInto`), which exists nowhere else in EmptyOS — every other
 * voice path routes a transcript to an intent/command, not into a field.
 *
 * STT is the in-browser Web Speech API (v1). The transcript polish + dark-flag
 * gate live in the `dictation` backend (`/dictation/api/{config,cleanup}`).
 *
 * Public API (window.EOS.dictate):
 *   insertInto(el, text)   insert text at el's caret (the tested primitive)
 *   reload()               re-fetch config + (un)bind the hotkey
 *   status()               { enabled, listening, hotkey }
 */
(function () {
    if (window.EOS_DICTATE_LOADED) return;
    window.EOS_DICTATE_LOADED = true;

    var EOS = window.EOS = window.EOS || {};
    var base = EOS.base || '';

    var cfg = null;                 // { enabled, hotkey, cleanup_threshold_words, mic_language, stt_provider }
    var chord = null;               // parsed hotkey
    var bound = false;
    var listening = false;
    var rec = null;                 // SpeechRecognition instance (per session)
    var finalText = '';             // accumulated final transcript this session
    var captured = null;            // { el, sel?, range? } — focus target at trigger time

    // ───────────────────────────────────────────────────────────── helpers
    function toast(msg) {
        if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast(msg);
        else try { console.log('[dictate] ' + msg); } catch (e) {}
    }

    function isEditable(el) {
        if (!el) return false;
        if (el.isContentEditable) return true;
        var tag = el.tagName;
        if (tag === 'TEXTAREA') return true;
        if (tag === 'INPUT') {
            var t = (el.type || 'text').toLowerCase();
            return ['text', 'search', 'url', 'email', 'tel', 'password', 'number', ''].indexOf(t) >= 0;
        }
        return false;
    }

    function parseChord(s) {
        var parts = String(s || 'ctrl+shift+d').toLowerCase().split('+')
            .map(function (p) { return p.trim(); }).filter(Boolean);
        var c = { ctrl: false, shift: false, alt: false, meta: false, key: '' };
        parts.forEach(function (p) {
            if (p === 'ctrl' || p === 'control') c.ctrl = true;
            else if (p === 'shift') c.shift = true;
            else if (p === 'alt' || p === 'option') c.alt = true;
            else if (p === 'meta' || p === 'cmd' || p === 'win' || p === 'super') c.meta = true;
            else if (p === 'space' || p === 'spacebar') c.key = ' ';   // KeyboardEvent.key for the spacebar is " "
            else c.key = p;
        });
        if (!c.key) c.key = 'd';
        return c;
    }

    function chordMatch(e, c) {
        return !!e.ctrlKey === c.ctrl && !!e.shiftKey === c.shift &&
            !!e.altKey === c.alt && !!e.metaKey === c.meta &&
            (e.key || '').toLowerCase() === c.key;
    }

    // Any keyup that breaks the held chord ends push-to-talk.
    function isChordKey(e, c) {
        var k = (e.key || '').toLowerCase();
        if (k === c.key) return true;
        if (c.ctrl && k === 'control') return true;
        if (c.shift && k === 'shift') return true;
        if (c.alt && k === 'alt') return true;
        if (c.meta && (k === 'meta' || k === 'os')) return true;
        return false;
    }

    // ─────────────────────────────────────────────── insertion (the primitive)
    function insertInto(el, text) {
        if (!isEditable(el)) { toast('Focus a text field first'); return false; }
        text = text || '';
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            el.focus();
            var start = (el.selectionStart != null) ? el.selectionStart : el.value.length;
            var end = (el.selectionEnd != null) ? el.selectionEnd : el.value.length;
            if (typeof el.setRangeText === 'function') {
                el.setRangeText(text, start, end, 'end');
            } else {
                el.value = el.value.slice(0, start) + text + el.value.slice(end);
                var pos = start + text.length;
                try { el.setSelectionRange(pos, pos); } catch (e) {}
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
        }
        // contenteditable
        el.focus();
        var ok = false;
        try { ok = document.execCommand('insertText', false, text); } catch (e) { ok = false; }
        if (!ok) {
            var sel = window.getSelection();
            if (sel && sel.rangeCount) {
                var r = sel.getRangeAt(0);
                r.deleteContents();
                r.insertNode(document.createTextNode(text));
                r.collapse(false);
            } else {
                el.appendChild(document.createTextNode(text));
            }
        }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    }

    // Insert into the field captured at trigger time, restoring its caret first
    // (focus/selection may have drifted during the async polish round-trip).
    function insertAtCaptured(text) {
        if (!captured || !isEditable(captured.el)) { toast('Focus a text field first'); return false; }
        var el = captured.el;
        el.focus();
        if ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && captured.sel) {
            try { el.setSelectionRange(captured.sel.start, captured.sel.end); } catch (e) {}
        } else if (el.isContentEditable && captured.range) {
            var sel = window.getSelection();
            try { sel.removeAllRanges(); sel.addRange(captured.range); } catch (e) {}
        }
        return insertInto(el, text);
    }

    function captureSelection(el) {
        var cap = { el: el };
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            cap.sel = { start: el.selectionStart, end: el.selectionEnd };
        } else if (el.isContentEditable) {
            var sel = window.getSelection();
            if (sel && sel.rangeCount) cap.range = sel.getRangeAt(0).cloneRange();
        }
        return cap;
    }

    // ──────────────────────────────────── live streaming (write as you speak)
    // We own a contiguous region of the focused field and rewrite it on every
    // interim result, so words appear live. On finalize the same region is
    // swapped in-place for the polished text. For input/textarea the region is
    // [anchor, anchor + text.length]; for contenteditable it's a dedicated text node.
    var stream = null;   // { el, type:'input'|'ce', anchor?, node?, text }

    function streamStart(el) {
        try {
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                var s = (el.selectionStart != null) ? el.selectionStart : el.value.length;
                var e = (el.selectionEnd != null) ? el.selectionEnd : el.value.length;
                if (e > s && typeof el.setRangeText === 'function') el.setRangeText('', s, e, 'end');
                stream = { el: el, type: 'input', anchor: s, text: '' };
            } else if (el.isContentEditable) {
                el.focus();
                var node = document.createTextNode('');
                var sel = window.getSelection();
                if (sel && sel.rangeCount) {
                    var r = sel.getRangeAt(0);
                    r.deleteContents();
                    r.insertNode(node);
                    r.setStartAfter(node); r.collapse(true);
                    sel.removeAllRanges(); sel.addRange(r);
                } else {
                    el.appendChild(node);
                }
                stream = { el: el, type: 'ce', node: node, text: '' };
            }
        } catch (err) { stream = null; }
    }

    function streamWrite(text) {
        if (!stream) return false;
        var el = stream.el;
        try {
            if (stream.type === 'input') {
                el.setRangeText(text, stream.anchor, stream.anchor + stream.text.length, 'end');
            } else {
                stream.node.nodeValue = text;
                var sel = window.getSelection();
                var r = document.createRange();
                r.setStartAfter(stream.node); r.collapse(true);
                sel.removeAllRanges(); sel.addRange(r);
            }
        } catch (err) { return false; }
        stream.text = text;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    }

    function streamEnd() { stream = null; }

    // ───────────────────────────────────────────────────────────────── HUD
    var hudEl = null;
    function ensureHud() {
        if (hudEl) return hudEl;
        hudEl = document.createElement('div');
        hudEl.id = 'eos-dictate-hud';
        hudEl.className = 'eos-dictate-hud';
        hudEl.setAttribute('role', 'status');
        document.body.appendChild(hudEl);
        return hudEl;
    }
    function hud(state, text) {
        var h = ensureHud();
        h.dataset.state = state;
        h.classList.add('on');
        var dot = state === 'listening' ? '<span class="eos-dictate-dot"></span>' : '';
        h.innerHTML = dot + '<span class="eos-dictate-txt"></span>';
        h.querySelector('.eos-dictate-txt').textContent = text || '';
    }
    function hudClose() {
        if (hudEl) hudEl.classList.remove('on');
    }
    function hudFlash(msg) {
        hud('done', msg);
        setTimeout(hudClose, 850);
    }

    // ─────────────────────────────────────── floating mic button (click-to-dictate)
    // Anchors to whichever editable field is focused; click to start, click to stop.
    // The hotkey path still works in parallel — this is the no-chord alternative.
    var micEl = null;
    var micTarget = null;        // editable element the button is anchored to
    var micHideTimer = null;

    function ensureMic() {
        if (micEl) return micEl;
        micEl = document.createElement('button');
        micEl.type = 'button';
        micEl.id = 'eos-dictate-mic';
        micEl.className = 'eos-dictate-mic';
        micEl.setAttribute('aria-label', 'Dictate into this field');
        micEl.title = 'Click to dictate — speak, then click again to stop';
        micEl.innerHTML = '<span class="eos-dictate-mic-icon">🎤</span>';
        // Press without stealing focus/selection from the field.
        micEl.addEventListener('mousedown', function (e) { e.preventDefault(); });
        micEl.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            if (listening) { stopListening(); return; }
            if (micTarget && isEditable(micTarget)) {
                try { micTarget.focus(); } catch (err) {}
                startListening();
            }
        });
        document.body.appendChild(micEl);
        return micEl;
    }

    function positionMic() {
        if (!micEl || !micTarget || !micTarget.isConnected) return;
        var r = micTarget.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) { hideMic(); return; }
        micEl.style.top = (r.bottom - 34) + 'px';
        micEl.style.left = (r.right - 38) + 'px';
    }

    function showMic(el) {
        if (micHideTimer) { clearTimeout(micHideTimer); micHideTimer = null; }
        micTarget = el;
        ensureMic();
        positionMic();
        micEl.classList.add('on');
    }

    function hideMic() {
        if (listening) return;             // stay visible while recording
        if (micEl) micEl.classList.remove('on');
        micTarget = null;
    }

    function setMicState(on) {
        if (micEl) micEl.classList.toggle('recording', !!on);
    }

    function onFocusIn(e) {
        if (!cfg || !cfg.enabled) return;
        var el = e.target;
        if (el === micEl || (micEl && micEl.contains(el))) return;
        if (hudEl && hudEl.contains(el)) return;
        if (isEditable(el)) showMic(el); else hideMic();
    }
    function onFocusOut() {
        // Delay so clicking the mic (which blurs the field) doesn't hide it first.
        if (micHideTimer) clearTimeout(micHideTimer);
        micHideTimer = setTimeout(hideMic, 150);
    }
    function onReposition() {
        if (micEl && micEl.classList.contains('on')) positionMic();
    }

    // ───────────────────────────────────────────────────── speech recognition
    function startListening() {
        if (listening) return;
        var el = document.activeElement;
        if (!isEditable(el)) { toast('Focus a text field first'); return; }
        captured = captureSelection(el);
        streamStart(el);
        if (cfg && cfg.stt_provider === 'server') { startServerRecording(); return; }
        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) { toast('No speech recognition in this browser (try Chrome or Edge)'); return; }
        finalText = '';
        try { rec = new SR(); } catch (e) { toast('Microphone unavailable'); return; }
        rec.lang = (cfg && cfg.mic_language) || 'en-US';
        rec.interimResults = true;
        rec.continuous = true;
        rec.onresult = function (ev) {
            var interim = '';
            for (var i = ev.resultIndex; i < ev.results.length; i++) {
                var r = ev.results[i];
                if (r.isFinal) finalText += r[0].transcript;
                else interim += r[0].transcript;
            }
            var live = (finalText + interim).replace(/^\s+/, '');   // write into the field live
            streamWrite(live);
            hud('listening', live.trim() || 'Listening…');
        };
        rec.onerror = function (ev) {
            if (ev.error !== 'no-speech' && ev.error !== 'aborted') toast('Speech error: ' + ev.error);
        };
        rec.onend = function () { finalize(); };
        listening = true;
        setMicState(true);
        try { rec.start(); } catch (e) {}
        hud('listening', 'Listening…');
    }

    function stopListening() {
        if (!listening) return;
        if (cfg && cfg.stt_provider === 'server') { stopServerRecording(); return; }
        listening = false;
        try { rec && rec.stop(); } catch (e) {}   // finalize() runs from onend
    }

    // ─────────────────────────── server-side STT (MediaRecorder -> /api/transcribe)
    // No live interim — the blob is transcribed by the listen chain (Whisper) on
    // stop. The recorded region (streamStart) receives the final text in one write.
    var mediaRec = null, mediaChunks = [], mediaStream = null;

    function cleanupMedia() {
        try { mediaStream && mediaStream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
        mediaStream = null; mediaRec = null; mediaChunks = [];
    }

    function startServerRecording() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            toast('Audio recording not supported in this browser'); streamEnd(); return;
        }
        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
            mediaStream = s; mediaChunks = [];
            try { mediaRec = new MediaRecorder(s); } catch (e) { toast('Microphone unavailable'); cleanupMedia(); streamEnd(); return; }
            mediaRec.ondataavailable = function (ev) { if (ev.data && ev.data.size) mediaChunks.push(ev.data); };
            mediaRec.onstop = function () { serverFinalize(); };
            listening = true; setMicState(true);
            try { mediaRec.start(); } catch (e) {}
            hud('listening', 'Recording…');
        }).catch(function () { toast('Microphone permission denied'); streamEnd(); });
    }

    function stopServerRecording() {
        if (!listening) return;
        listening = false;
        try { mediaRec && mediaRec.state !== 'inactive' && mediaRec.stop(); } catch (e) {}  // serverFinalize() from onstop
    }

    async function transcribe(blob) {
        var fd = new FormData();
        fd.append('audio', blob, 'dictation.webm');
        try {
            var r = await fetch(base + '/dictation/api/transcribe', { method: 'POST', body: fd })
                .then(function (x) { return x.json(); });
            return (r && r.text) ? r.text : '';
        } catch (e) { return ''; }
    }

    async function serverFinalize() {
        if (finalizing) return;
        finalizing = true;
        try {
            var type = (mediaRec && mediaRec.mimeType) || 'audio/webm';
            var blob = new Blob(mediaChunks, { type: type });
            cleanupMedia();
            if (!blob.size) { streamEnd(); hudClose(); return; }
            hud('polishing', 'Transcribing…');
            var text = (await transcribe(blob) || '').trim();
            if (!text) { streamEnd(); hudFlash('Nothing heard'); return; }
            var threshold = (cfg && cfg.cleanup_threshold_words) || 6;
            var cleaned = text;
            if (text.split(/\s+/).length > threshold) {
                hud('polishing', 'Polishing…');
                cleaned = await cleanup(text);
            }
            if (stream) { streamWrite(cleaned); streamEnd(); }
            else { insertAtCaptured(cleaned); }
            hudFlash('Inserted');
        } finally {
            finalizing = false;
            setMicState(false);
            if (isEditable(document.activeElement)) showMic(document.activeElement);
            else hideMic();
        }
    }

    var finalizing = false;
    async function finalize() {
        if (finalizing) return;
        finalizing = true;
        try {
            var raw = (finalText || (stream && stream.text) || '').trim();
            if (!raw) { streamEnd(); hudClose(); return; }
            var threshold = (cfg && cfg.cleanup_threshold_words) || 6;
            var cleaned = raw;
            if (raw.split(/\s+/).length > threshold) {
                hud('polishing', 'Polishing…');
                cleaned = await cleanup(raw);
            }
            // Swap the live (raw) streamed text in-place for the polished version.
            if (stream) { streamWrite(cleaned); streamEnd(); }
            else { insertAtCaptured(cleaned); }
            hudFlash('Polished');
        } finally {
            finalizing = false;
            setMicState(false);
            // Re-anchor or retire the button depending on where focus landed.
            if (isEditable(document.activeElement)) showMic(document.activeElement);
            else hideMic();
        }
    }

    async function cleanup(text) {
        try {
            var r;
            if (EOS.post) r = await EOS.post('/dictation/api/cleanup', { text: text });
            else r = await fetch(base + '/dictation/api/cleanup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text })
            }).then(function (x) { return x.json(); });
            return (r && r.cleaned) ? r.cleaned : text;
        } catch (e) { return text; }
    }

    // ─────────────────────────────────────────────────────── key bindings
    function onKeyDown(e) {
        if (!cfg || !cfg.enabled || !chord) return;
        if (e.repeat) return;                      // ignore auto-repeat while held
        if (!chordMatch(e, chord)) return;
        var el = document.activeElement;
        if (hudEl && hudEl.contains(el)) return;   // don't trigger from inside the HUD
        e.preventDefault();                        // suppress the browser's own chord
        e.stopPropagation();
        startListening();
    }

    function onKeyUp(e) {
        if (!listening || !chord) return;
        if (isChordKey(e, chord)) stopListening();
    }

    function bind() {
        if (bound) return;
        window.addEventListener('keydown', onKeyDown, true);
        window.addEventListener('keyup', onKeyUp, true);
        document.addEventListener('focusin', onFocusIn, true);
        document.addEventListener('focusout', onFocusOut, true);
        window.addEventListener('scroll', onReposition, true);
        window.addEventListener('resize', onReposition, true);
        bound = true;
        if (isEditable(document.activeElement)) showMic(document.activeElement);
    }
    function unbind() {
        if (!bound) return;
        window.removeEventListener('keydown', onKeyDown, true);
        window.removeEventListener('keyup', onKeyUp, true);
        document.removeEventListener('focusin', onFocusIn, true);
        document.removeEventListener('focusout', onFocusOut, true);
        window.removeEventListener('scroll', onReposition, true);
        window.removeEventListener('resize', onReposition, true);
        listening = false;
        hideMic();
        bound = false;
    }

    function applyConfig(c) {
        cfg = c || {};
        chord = parseChord(cfg.hotkey);
        if (cfg.enabled) bind(); else unbind();
    }

    async function loadConfig() {
        try {
            var c = EOS.api ? await EOS.api('/dictation/api/config')
                : await fetch(base + '/dictation/api/config').then(function (r) { return r.json(); });
            applyConfig(c || { enabled: false });
        } catch (e) { applyConfig({ enabled: false }); }
    }

    // ───────────────────────────────────────────────────────── public API
    EOS.dictate = {
        insertInto: insertInto,
        reload: loadConfig,
        status: function () {
            return { enabled: !!(cfg && cfg.enabled), listening: listening, hotkey: cfg && cfg.hotkey };
        }
    };

    loadConfig();
})();
