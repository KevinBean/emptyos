/* Active Listening (Step 5) — YouTube IFrame API + synced transcript.
 *
 * Loaded by `apps/video-digest/pages/index.html` as a deferred script.
 * Exposes a small surface on `window.VL`:
 *
 *     VL.open(digestPath)   // mount the Listen UI for a digest
 *     VL.close()            // tear down and return to the digest list
 *
 * Internals are kept self-contained behind an IIFE so we don't leak state
 * into the existing index.html script block. `index.html` knows nothing
 * about IFrame API specifics — it just calls VL.open / VL.close.
 *
 * Design references:
 *   - .claude/rules/app-ui-patterns.md (hash-route detail view)
 *   - feedback_app_page_sibling_assets (absolute /video-digest/pages path)
 *   - feedback_lock_held_emit_deadlock (server emits via create_task — UI doesn't await)
 *   - Step 5 of the 7 Step Method for Changing Your Accent — listen-first
 *     with captions OFF by default; reveal on `C`.
 */
(function () {
    'use strict';

    var VL_STORAGE_CC = 'video-digest.cc';

    var state = {
        digestPath: '',
        videoId: '',
        videoTitle: '',
        sourceUrl: '',
        transcript: [],
        transcriptError: '',
        player: null,
        playerReady: false,
        currentLineIdx: -1,
        capturedWords: [],
        playSeconds: 0,
        lastTickTs: 0,
        captionsOn: false,
        tickIv: null,
        lastScrollAt: 0,
        popoverEl: null,
        mounted: false,
    };

    // ──────────────────────── YouTube IFrame API loader ────────────────────────
    // The API loads once per page; subsequent opens await the same Promise.

    var _ytReadyPromise = null;
    function ensureYouTubeApi() {
        if (_ytReadyPromise) return _ytReadyPromise;
        _ytReadyPromise = new Promise(function (resolve) {
            if (window.YT && window.YT.Player) {
                resolve();
                return;
            }
            // If another script already set onYouTubeIframeAPIReady, chain it.
            var prev = window.onYouTubeIframeAPIReady;
            window.onYouTubeIframeAPIReady = function () {
                if (typeof prev === 'function') {
                    try { prev(); } catch (e) {}
                }
                resolve();
            };
            // Inject the API script if not already present.
            if (!document.querySelector('script[src="https://www.youtube.com/iframe_api"]')) {
                var tag = document.createElement('script');
                tag.src = 'https://www.youtube.com/iframe_api';
                document.head.appendChild(tag);
            }
        });
        return _ytReadyPromise;
    }

    // ──────────────────────── Public API ────────────────────────

    // VL.open / VL.close ONLY handle UI mount/teardown. URL state is owned
    // by the EOS_UI.hashRoute instance in index.html (`_vlRoute`); the
    // route's onShow/onHide call into here. Calling VL.open() directly is
    // legal but bypasses URL state — prefer `openListen(path)` from index.html.
    function open(digestPath) {
        if (!digestPath) return;
        if (state.mounted && state.digestPath === digestPath) return;
        if (state.mounted) {
            teardown();
        }
        state.digestPath = digestPath;
        document.body.classList.add('vd-listen-active');
        loadAndMount();
    }

    function close() {
        teardown();
    }

    function teardown() {
        if (state.tickIv) {
            clearInterval(state.tickIv);
            state.tickIv = null;
        }
        if (state.player) {
            try { state.player.destroy(); } catch (e) {}
            state.player = null;
        }
        state.playerReady = false;
        state.transcript = [];
        state.transcriptError = '';
        state.currentLineIdx = -1;
        state.capturedWords = [];
        state.playSeconds = 0;
        state.lastTickTs = 0;
        state.popoverEl = null;
        state.mounted = false;
        document.body.classList.remove('vd-listen-active');
        var root = document.getElementById('vd-listen-root');
        if (root) root.innerHTML = '';
    }

    window.VL = {open: open, close: close, _state: state};

    // ──────────────────────── Load + mount flow ────────────────────────

    async function loadAndMount() {
        var root = document.getElementById('vd-listen-root');
        if (!root) return;
        root.innerHTML = '<div style="padding:48px;text-align:center;color:var(--text-muted)">Loading transcript…</div>';

        var payload;
        try {
            var resp = await fetch('/video-digest/api/listen?digest_path=' + encodeURIComponent(state.digestPath));
            payload = await resp.json();
        } catch (e) {
            root.innerHTML = '<div style="padding:48px;text-align:center;color:var(--danger)">Failed to load: ' + esc(String(e)) + '</div>';
            return;
        }
        if (payload.error) {
            root.innerHTML =
                '<div style="padding:24px;max-width:520px;margin:24px auto;background:var(--bg-card);border:1px solid var(--border);border-radius:10px">' +
                    '<h3 style="margin:0 0 8px;font-size:15px">Can\'t open this digest in Listen mode</h3>' +
                    '<p style="margin:0 0 12px;color:var(--text-muted);font-size:13px">' + esc(payload.error) + '</p>' +
                    '<button class="eos-btn-sm" onclick="closeListen()">Back</button>' +
                '</div>';
            return;
        }

        state.videoId = payload.video_id || '';
        state.videoTitle = payload.video_title || '';
        state.sourceUrl = payload.source_url || '';
        state.transcript = (payload.transcript || []).map(function (t, i) {
            return {idx: i, text: String(t.text || ''), start: Number(t.start || 0), duration: Number(t.duration || 0)};
        });
        state.transcriptError = payload.transcript_error || '';
        state.captionsOn = (function () {
            try {
                var v = localStorage.getItem(VL_STORAGE_CC);
                if (v === null) return false; // Step 5: default OFF
                return v === '1';
            } catch (e) { return false; }
        })();
        state.mounted = true;
        renderUI();
        await mountPlayer();
        startTickLoop();
        attachHotkeys();
    }

    function renderUI() {
        var root = document.getElementById('vd-listen-root');
        if (!root) return;
        var transcriptCls = 'vl-transcript' + (state.captionsOn ? '' : ' captions-off');
        var transcriptHtml = state.transcript.length
            ? state.transcript.map(function (line, i) {
                return '<div class="vl-transcript-line" data-idx="' + i + '" data-start="' + line.start + '">' +
                    '<span class="vl-time">' + fmtTime(line.start) + '</span>' +
                    tokeniseLine(line.text) +
                    '</div>';
            }).join('')
            : '<div style="padding:14px;color:var(--text-muted)">' +
                (state.transcriptError ? esc(state.transcriptError) : 'No transcript available — listen only.') +
              '</div>';

        root.innerHTML =
            '<div class="vl-header">' +
                '<button class="vl-back" onclick="closeListen()">← Back</button>' +
                '<h2 title="' + escAttr(state.videoTitle) + '">' + esc(state.videoTitle || 'Listening session') + '</h2>' +
            '</div>' +
            '<div class="vl-body">' +
                '<div class="vl-player-col">' +
                    '<div id="vl-iframe-wrap"><div id="vl-iframe-host"></div></div>' +
                    '<div class="vl-controls">' +
                        '<button id="vl-play">▶ Play / Pause</button>' +
                        '<button id="vl-replay" title="Replay last 5 seconds (R)">⟲ −5s</button>' +
                        '<button id="vl-captions" title="Toggle captions (C)">' + (state.captionsOn ? '👁 Captions ON' : '👁 Captions OFF') + '</button>' +
                        '<button class="vl-end-btn" id="vl-end">End session</button>' +
                    '</div>' +
                    '<div class="vl-status" id="vl-status">Listening: 0:00</div>' +
                    '<div class="vl-hotkey-hint">' +
                        '<kbd>Space</kbd> play/pause · <kbd>R</kbd> replay −5s · <kbd>C</kbd> captions · ' +
                        '<kbd>↑</kbd>/<kbd>↓</kbd> prev/next line · <kbd>Esc</kbd> close popover · click a word to look it up' +
                    '</div>' +
                '</div>' +
                '<div class="vl-transcript-col">' +
                    '<div class="' + transcriptCls + '" id="vl-transcript">' + transcriptHtml + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="vl-words-panel" id="vl-words-panel">' +
                '<div class="vl-words-label">Captured words (0)</div>' +
                '<div class="vl-words-list" id="vl-words-list"></div>' +
            '</div>';

        // Wire delegated click on the transcript
        var tEl = document.getElementById('vl-transcript');
        tEl.addEventListener('click', onTranscriptClick);

        document.getElementById('vl-play').addEventListener('click', togglePlay);
        document.getElementById('vl-replay').addEventListener('click', replayLast5);
        document.getElementById('vl-captions').addEventListener('click', toggleCaptions);
        document.getElementById('vl-end').addEventListener('click', endSessionPrompt);
    }

    async function mountPlayer() {
        if (!state.videoId) return;
        await ensureYouTubeApi();
        // Player creation needs the host div to exist; renderUI() already did that.
        state.player = new YT.Player('vl-iframe-host', {
            videoId: state.videoId,
            host: 'https://www.youtube-nocookie.com',
            playerVars: {
                cc_load_policy: state.captionsOn ? 1 : 0,
                modestbranding: 1,
                rel: 0,
                playsinline: 1,
            },
            events: {
                onReady: function () {
                    state.playerReady = true;
                    state.lastTickTs = Date.now();
                },
                onStateChange: function () { state.lastTickTs = Date.now(); },
            },
        });
    }

    // ──────────────────────── Sync tick loop ────────────────────────

    function startTickLoop() {
        if (state.tickIv) clearInterval(state.tickIv);
        state.tickIv = setInterval(tick, 250);
    }

    function tick() {
        if (!state.playerReady || !state.player) return;
        var now = Date.now();
        var dtMs = now - state.lastTickTs;
        state.lastTickTs = now;
        // Only accumulate while playing (YT.PlayerState.PLAYING === 1)
        try {
            if (state.player.getPlayerState && state.player.getPlayerState() === 1) {
                // Bound dt at 2× our tick interval to handle background-tab throttling
                state.playSeconds += Math.min(dtMs, 500) / 1000;
                updateStatus();
                syncTranscript();
            }
        } catch (e) {}
    }

    function syncTranscript() {
        if (!state.transcript.length) return;
        var t;
        try { t = state.player.getCurrentTime(); } catch (e) { return; }
        var idx = findLineForTime(t);
        if (idx !== state.currentLineIdx) {
            setCurrentLine(idx);
        }
    }

    function findLineForTime(t) {
        // Monotonic search from the current index forward (handles play-forward)
        // with a small backward fallback (handles seeks). Round to int seconds —
        // float matching is brittle.
        var lines = state.transcript;
        if (!lines.length) return -1;
        var ts = Math.floor(t);
        // Try forward scan from current
        var i = Math.max(0, state.currentLineIdx);
        while (i < lines.length - 1 && Math.floor(lines[i + 1].start) <= ts) i++;
        // If we overshot (seek backward), scan back
        if (Math.floor(lines[i].start) > ts) {
            while (i > 0 && Math.floor(lines[i].start) > ts) i--;
        }
        return i;
    }

    function setCurrentLine(idx) {
        var prev = document.querySelector('.vl-transcript-line.current');
        if (prev) prev.classList.remove('current');
        state.currentLineIdx = idx;
        if (idx < 0) return;
        var el = document.querySelector('.vl-transcript-line[data-idx="' + idx + '"]');
        if (!el) return;
        el.classList.add('current');
        // Throttle to 1×/sec. Scroll the transcript box's own scrollbar
        // (not the page) so the sticky player stays put; center the line
        // within the visible transcript area.
        // `scrollIntoView({block:'center'})` mis-behaved here because it
        // also scrolled the page, which on narrow screens slid the line
        // up under the sticky player.
        var now = Date.now();
        if (now - state.lastScrollAt > 1000) {
            state.lastScrollAt = now;
            var box = el.closest('.vl-transcript');
            if (box) {
                // `offsetTop` is relative to offsetParent — when the
                // transcript box isn't its own positioned ancestor, the
                // numbers can be wildly off. getBoundingClientRect is
                // viewport-relative for both, so the math is robust:
                //   element-top-in-box-content = element.viewport.top
                //                              - box.viewport.top
                //                              + box.scrollTop
                // Then we offset by half the box height (true center) plus
                // half the line height so the line's center lands at center.
                var elRect = el.getBoundingClientRect();
                var boxRect = box.getBoundingClientRect();
                var elTopInBoxContent = elRect.top - boxRect.top + box.scrollTop;
                var targetScroll = elTopInBoxContent - (box.clientHeight / 2) + (elRect.height / 2);
                box.scrollTo({ top: Math.max(0, targetScroll), behavior: 'smooth' });
            } else {
                el.scrollIntoView({block: 'center', behavior: 'smooth'});
            }
        }
    }

    function updateStatus() {
        var el = document.getElementById('vl-status');
        if (el) el.textContent = 'Listening: ' + fmtTime(state.playSeconds);
    }

    // ──────────────────────── Interactions ────────────────────────

    function onTranscriptClick(ev) {
        var wordEl = ev.target.closest && ev.target.closest('.vl-word');
        if (wordEl) {
            ev.stopPropagation();
            handleWordClick(wordEl);
            return;
        }
        var lineEl = ev.target.closest && ev.target.closest('.vl-transcript-line');
        if (lineEl) {
            var start = parseFloat(lineEl.getAttribute('data-start') || '0');
            seekAndPlay(start);
        }
    }

    function seekAndPlay(t) {
        if (!state.player || !state.playerReady) return;
        try {
            state.player.seekTo(Math.max(0, t), true);
            state.player.playVideo();
        } catch (e) {}
    }

    function togglePlay() {
        if (!state.player || !state.playerReady) return;
        try {
            var s = state.player.getPlayerState();
            if (s === 1) state.player.pauseVideo();
            else state.player.playVideo();
        } catch (e) {}
    }

    function replayLast5() {
        if (!state.player || !state.playerReady) return;
        try {
            var t = state.player.getCurrentTime();
            state.player.seekTo(Math.max(0, t - 5), true);
            state.player.playVideo();
        } catch (e) {}
    }

    function toggleCaptions() {
        state.captionsOn = !state.captionsOn;
        try { localStorage.setItem(VL_STORAGE_CC, state.captionsOn ? '1' : '0'); } catch (e) {}
        var t = document.getElementById('vl-transcript');
        if (t) t.classList.toggle('captions-off', !state.captionsOn);
        var btn = document.getElementById('vl-captions');
        if (btn) btn.textContent = state.captionsOn ? '👁 Captions ON' : '👁 Captions OFF';
        // Also flip the in-player captions if possible
        try {
            if (state.captionsOn) state.player.loadModule('captions');
            else state.player.unloadModule('captions');
        } catch (e) {}
    }

    async function handleWordClick(el) {
        var raw = (el.getAttribute('data-w') || el.textContent || '').toLowerCase();
        // Strip surrounding punctuation
        var word = raw.replace(/^[^a-z']+|[^a-z']+$/g, '');
        if (!word) return;
        // Auto-pause for active reflection
        try { if (state.player && state.playerReady) state.player.pauseVideo(); } catch (e) {}
        showPopover(el, word, 'Looking up…');
        var data = null;
        try {
            var resp = await fetch('/dictionary/api/lookup?word=' + encodeURIComponent(word));
            data = await resp.json();
        } catch (e) {
            data = {error: 'lookup failed: ' + e};
        }
        renderPopoverContent(el, word, data);
    }

    function showPopover(near, word, body) {
        closePopover();
        var pop = document.createElement('div');
        pop.className = 'vl-popover';
        pop.innerHTML =
            '<div class="vl-pop-word">' + esc(word) + '</div>' +
            '<div class="vl-pop-def">' + esc(body) + '</div>';
        document.body.appendChild(pop);
        positionPopover(pop, near);
        state.popoverEl = pop;
    }

    function renderPopoverContent(near, word, data) {
        closePopover();
        var def = '';
        var phonetic = '';
        if (data && !data.error) {
            phonetic = data.phonetic ? esc(data.phonetic) : '';
            def = [data.part_of_speech, data.definition].filter(Boolean).map(esc).join(' · ');
            if (data.chinese) def += '<div style="color:var(--text-muted);margin-top:4px">' + esc(data.chinese) + '</div>';
        } else {
            def = '<span style="color:var(--text-muted)">' + esc((data && data.error) || 'No definition found') + '</span>';
        }
        var pop = document.createElement('div');
        pop.className = 'vl-popover';
        pop.innerHTML =
            '<div class="vl-pop-word">' + esc(word) + '</div>' +
            (phonetic ? '<div class="vl-pop-phonetic">' + phonetic + '</div>' : '') +
            '<div class="vl-pop-def">' + def + '</div>' +
            '<div class="vl-pop-actions">' +
                '<button class="vl-pop-primary" id="vl-pop-resume">▶ Resume</button>' +
                '<button id="vl-pop-save">+ Save word</button>' +
                '<button id="vl-pop-close" style="margin-left:auto">Close</button>' +
            '</div>';
        document.body.appendChild(pop);
        positionPopover(pop, near);
        state.popoverEl = pop;
        document.getElementById('vl-pop-resume').addEventListener('click', function () { closePopover(); resumePlay(); });
        document.getElementById('vl-pop-save').addEventListener('click', function () { saveWord(word, data); });
        document.getElementById('vl-pop-close').addEventListener('click', closePopover);
    }

    function positionPopover(pop, near) {
        var rect = near.getBoundingClientRect();
        var pw = pop.offsetWidth || 280;
        var ph = pop.offsetHeight || 120;
        var x = rect.left + rect.width / 2 - pw / 2;
        var y = rect.bottom + 8;
        x = Math.max(8, Math.min(x, window.innerWidth - pw - 8));
        if (y + ph > window.innerHeight - 8) y = rect.top - ph - 8;
        pop.style.left = x + 'px';
        pop.style.top = y + 'px';
    }

    function closePopover() {
        if (state.popoverEl && state.popoverEl.parentNode) {
            state.popoverEl.parentNode.removeChild(state.popoverEl);
        }
        state.popoverEl = null;
    }

    function resumePlay() {
        try { if (state.player && state.playerReady) state.player.playVideo(); } catch (e) {}
    }

    async function saveWord(word, lookupData) {
        // POST to dictionary/api/save — this fires `dictionary:word_saved`
        // (which the english app already listens to). We also keep the word
        // in our local capturedWords so End-session can pass them along.
        try {
            await fetch('/dictionary/api/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    word: word,
                    phonetic: (lookupData && lookupData.phonetic) || '',
                    definition: (lookupData && lookupData.definition) || '',
                    example: (lookupData && lookupData.example) || '',
                    part_of_speech: (lookupData && lookupData.part_of_speech) || '',
                }),
            });
        } catch (e) {
            // Save failure shouldn't block the listening flow.
        }
        if (state.capturedWords.indexOf(word) === -1) {
            state.capturedWords.push(word);
            renderCapturedWords();
        }
        if (window.EOS && EOS.toast) EOS.toast('Saved: ' + word, 'ok');
        closePopover();
        resumePlay();
    }

    function renderCapturedWords() {
        var panel = document.getElementById('vl-words-panel');
        var list = document.getElementById('vl-words-list');
        if (!panel || !list) return;
        if (!state.capturedWords.length) {
            panel.classList.remove('has-words');
            return;
        }
        panel.classList.add('has-words');
        panel.querySelector('.vl-words-label').textContent =
            'Captured words (' + state.capturedWords.length + ')';
        list.innerHTML = state.capturedWords.map(function (w) {
            return '<button class="vl-word-pill" data-w="' + escAttr(w) + '">' + esc(w) + '</button>';
        }).join('');
        Array.prototype.forEach.call(list.querySelectorAll('.vl-word-pill'), function (b) {
            b.addEventListener('click', function () {
                handleWordPillClick(b.getAttribute('data-w') || '');
            });
        });
    }

    async function handleWordPillClick(word) {
        if (!word) return;
        var data = null;
        try {
            var resp = await fetch('/dictionary/api/lookup?word=' + encodeURIComponent(word));
            data = await resp.json();
        } catch (e) { data = {error: 'lookup failed'}; }
        // Anchor on the words-panel since the original word el may not exist
        var anchor = document.getElementById('vl-words-panel');
        renderPopoverContent(anchor, word, data);
    }

    // ──────────────────────── End session ────────────────────────

    function endSessionPrompt() {
        // Pause and show summary modal. EOS_UI.modal supports
        // {title, body, width, onClose} — buttons live inside the body and
        // are wired via addEventListener after the modal mounts. (An earlier
        // version passed an `actions` array which EOS_UI ignored, leaving
        // the modal with only an X close and no save path.)
        try { if (state.player && state.playerReady) state.player.pauseVideo(); } catch (e) {}
        var mins = Math.max(1, Math.round(state.playSeconds / 60));

        var summary =
            '<div style="margin-bottom:10px"><b>' + mins + ' min</b> active listening</div>' +
            '<div style="font-size:13px;color:var(--text-muted);margin-bottom:6px">' +
                'Captured words (' + state.capturedWords.length + ')</div>' +
            '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:14px">' +
                (state.capturedWords.length
                    ? state.capturedWords.map(function (w) {
                        return '<span style="font-size:12px;padding:2px 8px;border-radius:10px;background:var(--bg-card);border:1px solid var(--border)">' + esc(w) + '</span>';
                    }).join('')
                    : '<span style="font-size:13px;color:var(--text-muted)">no words saved</span>') +
            '</div>' +
            '<div style="display:flex;gap:8px;justify-content:flex-end;border-top:1px solid var(--border);padding-top:12px;margin-top:4px">' +
                '<button class="eos-btn-sm" data-vl-action="cancel">Keep listening</button>' +
                '<button class="eos-btn-primary" data-vl-action="end">End session</button>' +
            '</div>';

        var modal = EOS_UI.modal({
            title: 'End listening session?',
            body: summary,
            width: '480px',
        });
        var cancelBtn = modal.querySelector('[data-vl-action="cancel"]');
        var endBtn = modal.querySelector('[data-vl-action="end"]');
        if (cancelBtn) cancelBtn.addEventListener('click', function () { EOS_UI.closeModal(); });
        if (endBtn) endBtn.addEventListener('click', function () { EOS_UI.closeModal(); endSession(); });
    }

    async function endSession() {
        var seconds = Math.max(1, Math.round(state.playSeconds));
        var payload = {
            digest_path: state.digestPath,
            seconds: seconds,
            words_captured: state.capturedWords.slice(),
        };
        try {
            var resp = await fetch('/video-digest/api/listen/end', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            var data = await resp.json();
            if (data.ok) {
                if (window.EOS && EOS.toast) {
                    EOS.toast('Session logged: ' + data.minutes + ' min', 'ok');
                }
            } else if (window.EOS && EOS.toast) {
                EOS.toast('Save failed: ' + (data.error || 'unknown'), 'error');
            }
        } catch (e) {
            if (window.EOS && EOS.toast) EOS.toast('Save failed: ' + e, 'error');
        }
        // Route through the index.html shim so the URL hash gets cleared too.
        // Falls back to local close() if the shim isn't loaded (e.g. tests).
        if (typeof window.closeListen === 'function') {
            window.closeListen();
        } else {
            close();
        }
    }

    // ──────────────────────── Hotkeys ────────────────────────

    function attachHotkeys() {
        document.addEventListener('keydown', keyHandler);
    }

    function keyHandler(ev) {
        if (!state.mounted) {
            document.removeEventListener('keydown', keyHandler);
            return;
        }
        // Don't hijack when user is typing in a text input
        var t = ev.target;
        if (t && t.tagName && ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(t.tagName) !== -1) return;
        if (state.popoverEl && ev.key === 'Escape') { closePopover(); ev.preventDefault(); return; }

        switch (ev.key) {
            case ' ':
            case 'Spacebar':
                ev.preventDefault();
                togglePlay();
                return;
            case 'r':
            case 'R':
                ev.preventDefault();
                replayLast5();
                return;
            case 'c':
            case 'C':
                ev.preventDefault();
                toggleCaptions();
                return;
            case 'Escape':
                if (state.popoverEl) closePopover();
                return;
            case 'ArrowDown':
                ev.preventDefault();
                stepLine(1);
                return;
            case 'ArrowUp':
                ev.preventDefault();
                stepLine(-1);
                return;
        }
    }

    function stepLine(delta) {
        if (!state.transcript.length) return;
        var next = Math.max(0, Math.min(state.transcript.length - 1, state.currentLineIdx + delta));
        if (next === state.currentLineIdx) return;
        seekAndPlay(state.transcript[next].start);
    }

    // ──────────────────────── Helpers ────────────────────────

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function fmtTime(t) {
        t = Math.max(0, Math.floor(Number(t) || 0));
        var m = Math.floor(t / 60);
        var s = t % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function tokeniseLine(text) {
        // Split into words / whitespace / punctuation, wrap words in clickable
        // spans. Preserves spacing exactly so the rendered line reads naturally.
        var out = [];
        var re = /([A-Za-z][A-Za-z'\-]*)|(\s+)|([^A-Za-z\s]+)/g;
        var m;
        while ((m = re.exec(text)) !== null) {
            if (m[1]) {
                var lc = m[1].toLowerCase();
                out.push('<span class="vl-word" data-w="' + escAttr(lc) + '">' + esc(m[1]) + '</span>');
            } else if (m[2]) {
                out.push(esc(m[2]));
            } else {
                out.push(esc(m[3]));
            }
        }
        return out.join('');
    }
})();
