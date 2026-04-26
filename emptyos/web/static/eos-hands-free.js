/**
 * EmptyOS Hands-Free Overlay
 *
 * Loaded globally by eos.js. Adds a floating chip that toggles gesture
 * push-to-talk + voice intents. MediaPipe (camera gesture) loads lazily
 * on first toggle; STT is chosen at runtime based on provider setting +
 * whether the browser is on the same host as the daemon.
 *
 * The backend at /hands-free/* is a thin support layer (config, LLM
 * cleanup, audit history). All real-time control is in this file.
 *
 * Public API (window.EOS.handsFree):
 *   toggle()          turn overlay on/off
 *   status()          { state, config, supported }
 *   on() / off()
 */
(function() {
    if (window.EOS_HANDSFREE_LOADED) return;
    window.EOS_HANDSFREE_LOADED = true;

    var EOS = window.EOS = window.EOS || {};
    EOS.base = EOS.base || '';

    // ──────────────────────────────────────────────────────────── state
    var config = null;
    var state = 'off';         // off | loading | idle | triggering | listening | thinking | confirm | firing | undo_window
    var recognizer = null;      // MediaPipe GestureRecognizer
    var drawingUtils = null;
    var stream = null;
    var video = null;
    var miniCanvas = null;
    var miniCtx = null;
    var loopRaf = null;
    var speechRec = null;
    var confirmTimer = null;
    var undoTimer = null;
    var pendingIntent = null;     // { transcript, intent, target, fire, undo }
    var lastFired = null;         // same shape, retained for undo window

    var gestureCandidate = { name: null, since: 0 };
    var lastGestureAction = { name: null, ts: 0 };

    var MIN_CONF = 0.6;
    var CONFIRM_HOLD_MS = 250;
    var CONFIRM_COOLDOWN_MS = 900;
    var OS_DICTATE_SETTLE_MS = 1500;
    var GESTURE_ACTION_HOLD_MS = 300;
    var EXIT_HOLD_MS = 2000;

    // Per-page gesture overrides. Pages call EOS.handsFree.registerGesture(name, fn, desc)
    // on load; on unload / navigation the registry resets because it lives in window scope
    // alongside the overlay's other state.
    var registeredGestures = {};
    // Flag: next listening session should bypass the palette + resolver and POST the
    // transcript straight to /quick-action/api/smart-add. Set by the Victory quick-capture path.
    var quickCaptureMode = false;

    // V8B — finger-tip scroll. When Pointing_Up is detected in idle, the index
    // fingertip Y (MediaPipe landmark 8, normalised [0,1]) becomes a cursor: any
    // displacement beyond the deadzone scrolls the page per-frame. refY is set
    // at activation — wherever the hand starts becomes "neutral". Grace period
    // covers single-frame recognition gaps.
    var scrollState = {active: false, refY: null, lastSeen: 0, lastFrameTs: 0, emaY: null, vel: 0, inertiaRaf: null, inertiaUntil: 0};
    var SCROLL_GRACE_MS = 200;
    var SCROLL_MAX_DISPLACEMENT = 0.22;
    var SCROLL_EMA_ALPHA = 0.25;          // smoothing for fingertip Y (overridden by config if set)
    var SCROLL_INERTIA_DECAY = 0.94;      // per-frame velocity decay when finger lost (overridden by config)
    var SCROLL_INERTIA_MS = 450;          // cap inertia window (overridden by config)

    // V9 — Cursor Mode (in-browser). Map fingertip to an on-page cursor and support
    // pinch-to-click or dwell-to-click. This never moves the OS pointer; it only
    // dispatches DOM click events within the current page.
    var CURSOR_ENABLED_DEFAULT = false;
    var CURSOR_EMA_ALPHA_DEFAULT = 0.25;
    var CURSOR_DWELL_MS_DEFAULT = 800;
    var CURSOR_PINCH_ON = 0.05;  // normalised distance (0..1) between thumb (4) and index (8)
    var CURSOR_PINCH_OFF = 0.07; // hysteresis to avoid chatter
    var cursorEl = null;
    var cursorState = { active: false, x: 0, y: 0, emaX: null, emaY: null, lastMoveTs: 0, lastClickTs: 0, pinched: false };


    // V3 — TTS read-back. currentUtterance is non-null while speechSynthesis is active;
    // isTtsSpeaking mirrors it for the chip visual (state machine stays unchanged, speaking
    // is an overlay effect that can occur during any state).
    var currentUtterance = null;
    var isTtsSpeaking = false;
    // lastQaAnswer holds the most recent /search/api/ask answer so the "Re-read" button
    // can replay it without re-fetching. V7 also retains lastQaQuestion so follow-up
    // queries ("tell me more", "why", "go on") can carry the previous turn as context.
    var lastQaAnswer = null;
    var lastQaQuestion = null;

    // V4 — proactive TTS. Poll the server for announcements since our last seen ts.
    // Only runs while hands-free is on (chip toggled). Announcements are gated
    // server-side (enabled flag + event allowlist + quiet hours + min gap); the
    // client only gates on voice_out + chip state.
    var PROACTIVE_POLL_MS = 4000;
    var proactiveSince = 0;
    var proactiveTimer = null;

    // V6 — ambient ritual. When the poll sees a `kind: "ritual"` entry the overlay
    // speaks the prompt, auto-starts a hidden-input listen on TTS end, and routes
    // the transcript to the ritual's configured action (capture / journal-milestone /
    // task). ritualActionPending signals handleTranscript to short-circuit; ritual-
    // ListenMode tells startListening to avoid the palette (os-native path).
    var ritualActionPending = null;
    var ritualListenMode = false;

    // V5 — read-aloud queue. Overlay pulls a feed from an app, reads items with TTS,
    // gestures navigate. readState lives here so gestures can mutate it during the
    // `reading` state. suppressNextOnEnd is flipped by cancelSpeak so a manually
    // cancelled utterance doesn't trigger the speakText onEnd callback (e.g. the
    // auto-advance in read mode).
    var readState = {source: null, items: [], index: 0, autoAdvance: true};
    var suppressNextOnEnd = false;
    var READ_AUTOADVANCE_PAUSE_MS = 900;
    var READ_SOURCES = {
        inbox:   '/quick-action/api/read-feed',
        tasks:   '/task/api/read-feed',
        journal: '/journal/api/read-feed',
        today:   '/journal/api/read-feed',
    };

    // ──────────────────────────────────────────────────────────── DOM
    var chip, chipLabel, preview, confirmCard, confirmTranscript, confirmIntent, confirmRing, confirmState;
    var cheatPanel, cheatList;

    function h(tag, attrs, inner) {
        var el = document.createElement(tag);
        if (attrs) for (var k in attrs) {
            if (k === 'style') el.setAttribute('style', attrs[k]);
            else if (k === 'class') el.className = attrs[k];
            else el.setAttribute(k, attrs[k]);
        }
        if (inner != null) el.innerHTML = inner;
        return el;
    }

    function buildChrome() {
        // chip
        chip = h('button', {id: 'eos-handsfree-chip', class: 'eos-fab-pill eos-hf-chip', 'aria-label': 'Hands-free mode'}, '');
        chipLabel = h('span', {class: 'eos-hf-chip-label'}, 'hands-free');
        var chipIcon = h('span', {class: 'eos-hf-chip-icon'}, '🖐');
        chip.appendChild(chipIcon);
        chip.appendChild(chipLabel);
        chip.addEventListener('click', onChipClick);

        // mini preview
        preview = h('div', {id: 'eos-hf-preview', class: 'eos-hf-preview'});
        video = h('video', {playsinline: '', muted: '', autoplay: ''});
        miniCanvas = h('canvas');
        preview.appendChild(video);
        preview.appendChild(miniCanvas);
        var previewBadge = h('div', {class: 'eos-hf-preview-badge'}, 'CAM');
        preview.appendChild(previewBadge);
        preview.style.display = 'none';
        miniCtx = miniCanvas.getContext('2d');

        // confirm card
        confirmCard = h('div', {id: 'eos-hf-confirm', class: 'eos-hf-confirm'});
        confirmCard.innerHTML =
            '<div class="hfc-head">' +
                '<svg class="hfc-ring" width="44" height="44" viewBox="0 0 44 44">' +
                    '<circle class="hfc-ring-bg" cx="22" cy="22" r="18" fill="none" stroke-width="3"/>' +
                    '<circle class="hfc-ring-fg" cx="22" cy="22" r="18" fill="none" stroke-width="3" transform="rotate(-90 22 22)"/>' +
                '</svg>' +
                '<div class="hfc-head-text">' +
                    '<div class="hfc-state" id="hfc-state">Heard</div>' +
                    '<div class="hfc-intent" id="hfc-intent">—</div>' +
                '</div>' +
                '<button class="hfc-close" data-act="cancel" aria-label="Cancel">✕</button>' +
            '</div>' +
            '<div class="hfc-transcript" id="hfc-transcript">—</div>' +
            '<div class="hfc-actions">' +
                '<button class="hfc-btn primary" data-act="approve">Approve</button>' +
                '<button class="hfc-btn" data-act="retry">Retry</button>' +
                '<button class="hfc-btn ghost" data-act="cancel">Cancel</button>' +
            '</div>' +
            '<div class="hfc-hint">👍 approve · 👎 retry · ✊ cancel · auto-fires when the ring completes</div>';
        confirmCard.style.display = 'none';
        confirmCard.addEventListener('click', onConfirmClick);
        confirmTranscript = confirmCard.querySelector('#hfc-transcript');
        confirmIntent = confirmCard.querySelector('#hfc-intent');
        confirmRing = confirmCard.querySelector('.hfc-ring-fg');
        confirmState = confirmCard.querySelector('#hfc-state');

        // cheat panel — shows current gesture bindings on whatever page you're on
        cheatPanel = h('div', {id: 'eos-hf-cheat', class: 'eos-hf-cheat'});
        cheatPanel.innerHTML =
            '<div class="hfc-cheat-head">' +
                '<span>Gestures</span>' +
                '<button class="hfc-cheat-toggle" data-act="toggle" aria-label="Collapse">–</button>' +
            '</div>' +
            '<div class="hfc-cheat-list" id="hfc-cheat-list"></div>';
        cheatPanel.style.display = 'none';
        cheatPanel.addEventListener('click', onCheatClick);
        cheatList = cheatPanel.querySelector('#hfc-cheat-list');

        // cursor overlay (hidden by default)
        cursorEl = h('div', {id: 'eos-hf-cursor', style: 'position:fixed; left:0; top:0; width:14px; height:14px; margin:-7px 0 0 -7px; border-radius:50%; border:2px solid var(--accent); background:color-mix(in srgb, var(--accent) 20%, transparent); pointer-events:none; z-index:2147483647; display:none;'});

        var dockTarget = document.getElementById('eos-fab-others') || document.body;
        dockTarget.appendChild(chip);
        document.body.appendChild(preview);
        document.body.appendChild(confirmCard);
        document.body.appendChild(cheatPanel);
        document.body.appendChild(cursorEl);

        renderChip();
    }

    // ──────────────────────────────────────────────────────────── cheat panel
    // Gestures mean different things in different states — idle's Pointing_Up is
    // "scroll page", reading's Pointing_Up is "next item". Keeping the cheat sheet
    // static is a lie. STATE_CHEATS rewrites the panel as the state machine moves.
    // Idle rows that carry `action: true` honour per-page registerGesture overrides
    // (scroll → "Complete top task" on /task/, etc.); other states use fixed labels
    // because gestures there aren't user-overridable.
    var STATE_CHEATS = {
        idle: [
            {em: '✋', g: 'Open_Palm',   def: 'Hold 1s → listen',        action: false},
            {em: '✌️', g: 'Victory',     def: 'Quick capture',           action: true},
            {em: '☝️', g: 'Pointing_Up', def: 'Hold → scroll (finger)',  action: true},
            {em: '🤟', g: 'ILoveYou',    def: 'App-registered',          action: true},
            {em: '✊', g: 'Closed_Fist',  def: 'Hold 2s → exit',          action: false},
        ],
        triggering: [  // transitional — same options as idle
            {em: '✋', g: 'Open_Palm',   def: 'Holding…',                action: false},
            {em: '✊', g: 'Closed_Fist',  def: 'Release to cancel',       action: false},
        ],
        listening: [
            {em: '🎙',  g: '(speak)',    def: 'Your voice is live',     action: false},
            {em: '✊', g: 'Closed_Fist',  def: 'Cancel listening',       action: false},
        ],
        thinking: [
            {em: '⋯', g: '(polishing)', def: 'LLM cleaning transcript…', action: false},
        ],
        confirm: [
            {em: '👍', g: 'Thumb_Up',    def: 'Confirm / fire now',     action: false},
            {em: '👎', g: 'Thumb_Down',  def: 'Retry',                   action: false},
            {em: '✊', g: 'Closed_Fist',  def: 'Cancel',                  action: false},
        ],
        firing: [
            {em: '⏳', g: '(firing)',    def: 'Dispatching intent…',    action: false},
        ],
        undo_window: [
            {em: '👎', g: 'Thumb_Down',  def: 'Undo last action',       action: false},
        ],
        reading: [
            {em: '☝️', g: 'Pointing_Up', def: 'Next item',              action: false},
            {em: '👎', g: 'Thumb_Down',  def: 'Previous / repeat',       action: false},
            {em: '✌️', g: 'Victory',     def: 'Act on current',         action: false},
            {em: '✊', g: 'Closed_Fist',  def: 'Stop reading',           action: false},
        ],
        speaking: [
            {em: '✊', g: 'Closed_Fist',  def: 'Interrupt speech',       action: false},
            {em: 'Esc', g: '(key)',      def: 'Interrupt speech',       action: false},
        ],
    };

    function renderCheatList() {
        if (!cheatList) return;
        var visualState = isTtsSpeaking ? 'speaking' : state;
        var rows = STATE_CHEATS[visualState] || STATE_CHEATS.idle;
        // In idle, surface Thumb_Up/Thumb_Down too when the page registered handlers
        // (they have no default meaning in idle, so they only appear when useful).
        if (visualState === 'idle') {
            rows = rows.slice();
            ['Thumb_Up', 'Thumb_Down'].forEach(function(g) {
                if (registeredGestures[g]) {
                    var em = g === 'Thumb_Up' ? '👍' : '👎';
                    rows.push({em: em, g: g, def: '', action: true});
                }
            });
        }
        var html = rows.map(function(row) {
            // Only idle rows respect per-page overrides; other states are fixed.
            var override = (visualState === 'idle' && row.action) ? registeredGestures[row.g] : null;
            var label = override ? override.desc : row.def;
            var badge = override ? '<span class="hfc-cheat-badge">this page</span>' : '';
            return '<div class="hfc-cheat-row">' +
                '<span class="em">' + row.em + '</span>' +
                '<span class="g">' + row.g.replace('_', ' ') + '</span>' +
                '<span class="a">' + escapeText(label) + badge + '</span>' +
            '</div>';
        }).join('');
        cheatList.innerHTML = html;
    }

    function escapeText(s) {
        var d = document.createElement('div');
        d.textContent = String(s || '');
        return d.innerHTML;
    }

    function onCheatClick(e) {
        var act = e.target && e.target.getAttribute('data-act');
        if (act === 'toggle') cheatPanel.classList.toggle('collapsed');
    }

    function showCheatPanel() {
        if (!cheatPanel) return;
        renderCheatList();
        cheatPanel.style.display = '';
    }

    function hideCheatPanel() {
        if (!cheatPanel) return;
        cheatPanel.style.display = 'none';
    }

    function renderChip() {
        if (!chip) return;
        // Speaking overlays the normal state visual — gets its own class so CSS pulses green.
        var visualState = isTtsSpeaking ? 'speaking' : state;
        chip.className = 'eos-fab-pill eos-hf-chip eos-hf-state-' + visualState;
        var map = {
            off: 'hands-free',
            loading: 'starting…',
            idle: 'idle',
            triggering: 'hold…',
            listening: 'listening',
            thinking: 'polishing…',
            confirm: 'confirm?',
            firing: 'firing…',
            undo_window: 'undo?',
            speaking: 'reading…',
            reading: 'read ▶',
        };
        if (chipLabel) chipLabel.textContent = map[visualState] || visualState;
    }

    function setState(next) {
        state = next;
        renderChip();
        // Keep the cheat panel in sync with what gestures actually do right now.
        if (cheatPanel && cheatPanel.style.display !== 'none') renderCheatList();
    }

    // ──────────────────────────────────────────────────────────── config
    function loadConfig() {
        return fetch(EOS.base + '/hands-free/api/config')
            .then(function(r) { return r.json(); })
            .then(function(c) { config = c; return c; })
            .catch(function() {
                config = {trigger_gesture: 'Open_Palm', hold_ms: 1000, cleanup_threshold_words: 8, auto_confirm_ms: 3000, stt_provider: 'web-speech', mic_language: 'en-US', os_dictate_supported: false};
                return config;
            });
    }

    function effectiveProvider() {
        if (!config) return 'web-speech';
        var host = (window.location.hostname || '').toLowerCase();
        var isLocal = (host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1');
        if (config.stt_provider === 'os-native' && config.os_dictate_supported && isLocal) return 'os-native';
        if (config.stt_provider === 'os-native') return 'web-speech'; // fallback
        return config.stt_provider;
    }

    // ──────────────────────────────────────────────────────────── toggle
    function onChipClick() {
        if (state === 'off') turnOn();
        else turnOff();
    }

    function turnOn() {
        if (state !== 'off') return;
        setState('loading');
        // Warm the palette action registry so the intent resolver can fuzzy-match apps.
        if (EOS.keys && EOS.keys._loadActions) { try { EOS.keys._loadActions(); } catch(e){} }
        loadConfig()
            .then(function() { return loadMediaPipe(); })
            .then(function() { return startCamera(); })
            .then(function() {
                preview.style.display = '';
                showCheatPanel();
                startProactivePoll();
                setState('idle');
                gestureLoop();
            })
            .catch(function(err) {
                console.error('[hands-free]', err);
                if (window.EOS_UI) EOS_UI.toast('Hands-free: ' + (err.message || err), false);
                turnOff();
            });
    }

    function turnOff() {
        setState('off');
        if (loopRaf) cancelAnimationFrame(loopRaf);
        loopRaf = null;
        if (stream) stream.getTracks().forEach(function(t) { t.stop(); });
        stream = null;
        if (video) video.srcObject = null;
        if (preview) preview.style.display = 'none';
        hideCheatPanel();
        stopProactivePoll();
        if (speechRec) { try { speechRec.abort(); } catch(e){} speechRec = null; }
        cancelSpeak();
        cancelConfirm();
        renderChip();
    }

    // ──────────────────────────────────────────────────────────── MediaPipe
    var mediapipeModule = null;

    function loadMediaPipe() {
        if (mediapipeModule) return Promise.resolve(mediapipeModule);
        return import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs')
            .then(function(mod) {
                mediapipeModule = mod;
                return mod.FilesetResolver.forVisionTasks(
                    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm'
                );
            })
            .then(function(vision) {
                return mediapipeModule.GestureRecognizer.createFromOptions(vision, {
                    baseOptions: {
                        modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task',
                        delegate: 'GPU',
                    },
                    runningMode: 'VIDEO',
                    numHands: 1,
                });
            })
            .then(function(rec) {
                recognizer = rec;
                drawingUtils = new mediapipeModule.DrawingUtils(miniCtx);
                return rec;
            });
    }

    function startCamera() {
        return navigator.mediaDevices.getUserMedia({video: {width: 640, height: 360}, audio: false})
            .then(function(s) {
                stream = s;
                video.srcObject = s;
                return video.play();
            })
            .then(function() {
                miniCanvas.width = 240;
                miniCanvas.height = 135;
            });
    }

    // ──────────────────────────────────────────────────────────── gesture loop
    function gestureLoop() {
        if (state === 'off') return;
        var ts = performance.now();
        if (video.readyState >= 2 && recognizer) {
            var res;
            try { res = recognizer.recognizeForVideo(video, ts); }
            catch(e) { loopRaf = requestAnimationFrame(gestureLoop); return; }
            drawMini(res);
            handleGesture(res, ts);
            updateFingerScroll(res, ts);
            updateCursorMode(res, ts);
        }
        loopRaf = requestAnimationFrame(gestureLoop);
    }

    // V8B — per-frame finger-tip scroll. Only active when Pointing_Up is detected
    // in idle/triggering state AND the page hasn't registered a handler for it.
    // Exits automatically after SCROLL_GRACE_MS of not seeing the gesture so brief
    // MediaPipe blips don't stop scroll mid-motion.
    function updateFingerScroll(res, ts) {
        if (!(config && config.finger_scroll_enabled !== false)) { if (scrollState.active) endFingerScroll(); return; }
        if (state !== 'idle' && state !== 'triggering') { if (scrollState.active) endFingerScroll(); return; }
        // Page-registered handler wins — if /task/ bound Pointing_Up to "complete top
        // task", we stay out of its way.
        if (registeredGestures['Pointing_Up']) { if (scrollState.active) endFingerScroll(); return; }
        var top = res && res.gestures && res.gestures[0] && res.gestures[0][0];
        var isPointing = top && top.categoryName === 'Pointing_Up' && top.score >= MIN_CONF;
        var lm = res && res.landmarks && res.landmarks[0];
        var tipY = (lm && lm[8]) ? lm[8].y : null;

        // If not confidently pointing, optionally continue with inertia for a brief window
        if (!isPointing || tipY == null) {
            if (scrollState.active) {
                var dtSecI = Math.min(0.1, Math.max(0.005, (ts - scrollState.lastFrameTs) / 1000));
                scrollState.lastFrameTs = ts;
                // Decay velocity and apply if still significant
                scrollState.vel *= SCROLL_INERTIA_DECAY;
                if (Math.abs(scrollState.vel) > 5 && (ts < scrollState.inertiaUntil)) {
                    window.scrollBy(0, scrollState.vel * dtSecI);
                } else if ((ts - scrollState.lastSeen) > SCROLL_GRACE_MS) {
                    endFingerScroll();
                }
            }
            return;
        }

        // Activation/init
        if (!scrollState.active) {
            scrollState.active = true;
            scrollState.refY = tipY;
            scrollState.emaY = tipY;
            scrollState.vel = 0;
            scrollState.inertiaUntil = ts + SCROLL_INERTIA_MS;
            scrollState.lastFrameTs = ts;
        }

        scrollState.lastSeen = ts;
        var deadzone = (config && Number(config.finger_scroll_deadzone)) || 0.06;
        var gain = (config && Number(config.finger_scroll_gain)) || 900;
        var emaAlpha = (config && Number(config.finger_scroll_smooth_alpha)) || SCROLL_EMA_ALPHA;
        var inertiaDecay = (config && Number(config.finger_scroll_inertia_decay)) || SCROLL_INERTIA_DECAY;
        var inertiaMs = (config && Number(config.finger_scroll_inertia_ms)) || SCROLL_INERTIA_MS;

        // Smooth fingertip signal
        if (scrollState.emaY == null) scrollState.emaY = tipY;
        else scrollState.emaY = emaAlpha * tipY + (1 - emaAlpha) * scrollState.emaY;

        // displacement > 0 means finger is below ref (intuition: scroll down)
        var displacement = scrollState.emaY - scrollState.refY;
        var abs = Math.abs(displacement);

        // Dynamic neutral recenter when hand hovers near center — prevents drift build-up
        if (abs < deadzone * 0.6) {
            // Slowly pull ref toward emaY
            scrollState.refY = 0.92 * scrollState.refY + 0.08 * scrollState.emaY;
        }

        if (abs < deadzone) { scrollState.lastFrameTs = ts; scrollState.vel *= Math.max(0.85, inertiaDecay); return; }

        // Nonlinear response curve: gentler near center, stronger near edges
        var span = (SCROLL_MAX_DISPLACEMENT - deadzone);
        var norm = Math.min(abs - deadzone, span) / span; // 0..1
        var curved = Math.pow(norm, 1.35); // ease-out
        var magnitude = Math.sign(displacement) * curved;

        var dtSec = Math.min(0.1, Math.max(0.005, (ts - scrollState.lastFrameTs) / 1000));
        scrollState.lastFrameTs = ts;

        // Target velocity in px/sec, blend to avoid spikes
        var targetVel = magnitude * gain;
        scrollState.vel = 0.6 * scrollState.vel + 0.4 * targetVel;
        var pixels = scrollState.vel * dtSec;
        window.scrollBy(0, pixels);

        // Extend inertia window while actively controlling
        scrollState.inertiaUntil = ts + inertiaMs;
    }

    function endFingerScroll() {
        scrollState.active = false;
        scrollState.refY = null;
        scrollState.emaY = null;
        scrollState.vel = 0;
        scrollState.inertiaUntil = 0;
    }

    // ──────────────────────────────────────────────────────────── Cursor Mode
    function updateCursorMode(res, ts) {
        if (!config) return;
        var enabled = !!config.cursor_enabled;
        if (!enabled || state !== 'idle') { if (cursorEl) cursorEl.style.display = 'none'; cursorState.active = false; return; }

        var lm = res && res.landmarks && res.landmarks[0];
        var p8 = lm && lm[8];
        var p4 = lm && lm[4];
        if (!p8 || !p4) { if (cursorEl) cursorEl.style.display = 'none'; cursorState.active = false; return; }

        // Map normalized landmark (x,y ∈ [0,1]) to viewport coordinates
        // Note: MediaPipe x is [0..1] left→right in the input frame; our mini preview may mirror,
        // but landmarks are already normalized to the original video coordinates.
        var vw = document.documentElement.clientWidth;
        var vh = document.documentElement.clientHeight;

        var alpha = Number(config.cursor_smooth_alpha) || CURSOR_EMA_ALPHA_DEFAULT;
        var x = Math.max(0, Math.min(1, p8.x));
        var y = Math.max(0, Math.min(1, p8.y));
        var cx = x * vw;
        var cy = y * vh;

        if (cursorState.emaX == null) { cursorState.emaX = cx; cursorState.emaY = cy; }
        else {
            cursorState.emaX = alpha * cx + (1 - alpha) * cursorState.emaX;
            cursorState.emaY = alpha * cy + (1 - alpha) * cursorState.emaY;
        }

        cursorState.x = cursorState.emaX;
        cursorState.y = cursorState.emaY;
        cursorState.lastMoveTs = ts;

        if (cursorEl) {
            cursorEl.style.display = 'block';
            cursorEl.style.transform = 'translate(' + Math.round(cursorState.x) + 'px,' + Math.round(cursorState.y) + 'px)';
        }

        // Pinch detection for click
        var dx = (p4.x - p8.x), dy = (p4.y - p8.y);
        var dist = Math.hypot(dx, dy);
        var pinchOn = Number(config.cursor_pinch_on) || CURSOR_PINCH_ON;
        var pinchOff = Number(config.cursor_pinch_off) || CURSOR_PINCH_OFF;
        if (!cursorState.pinched && dist <= pinchOn) {
            cursorState.pinched = true;
            doDomClick(cursorState.x, cursorState.y);
        } else if (cursorState.pinched && dist >= pinchOff) {
            cursorState.pinched = false;
        }

        // Optional dwell click
        var dwellMs = Number(config.cursor_dwell_ms) || 0;
        if (dwellMs > 0) {
            var still = (Math.hypot(cx - cursorState.emaX, cy - cursorState.emaY) < 6);
            if (still && (ts - cursorState.lastClickTs) > dwellMs && (ts - cursorState.lastMoveTs) > dwellMs) {
                doDomClick(cursorState.x, cursorState.y);
            }
        }
    }

    function doDomClick(x, y) {
        cursorState.lastClickTs = Date.now();
        var el = document.elementFromPoint(Math.round(x), Math.round(y));
        if (!el) return;
        // Synthesize a user-like click sequence
        var opts = {clientX: x, clientY: y, bubbles: true, cancelable: true, view: window};
        el.dispatchEvent(new MouseEvent('pointerdown', opts));
        el.dispatchEvent(new MouseEvent('mousedown', opts));
        el.dispatchEvent(new MouseEvent('mouseup', opts));
        el.dispatchEvent(new MouseEvent('click', opts));
        // Visual feedback
        if (cursorEl) {
            cursorEl.style.transition = 'transform 0s, background-color 0.12s';
            cursorEl.style.backgroundColor = 'color-mix(in srgb, var(--accent) 50%, transparent)';
            setTimeout(function(){ if (cursorEl) cursorEl.style.backgroundColor = 'color-mix(in srgb, var(--accent) 20%, transparent)'; }, 120);
        }
    }

    function drawMini(res) {
        if (!miniCtx) return;
        miniCtx.save();
        miniCtx.clearRect(0, 0, miniCanvas.width, miniCanvas.height);
        miniCtx.scale(-1, 1);
        miniCtx.translate(-miniCanvas.width, 0);
        miniCtx.drawImage(video, 0, 0, miniCanvas.width, miniCanvas.height);
        miniCtx.restore();
        if (res && res.landmarks && res.landmarks.length && mediapipeModule) {
            for (var i = 0; i < res.landmarks.length; i++) {
                drawingUtils.drawConnectors(res.landmarks[i], mediapipeModule.GestureRecognizer.HAND_CONNECTIONS,
                    {color: '#00E5FF', lineWidth: 2});
            }
        }
    }

    function handleGesture(res, ts) {
        var top = res && res.gestures && res.gestures[0] && res.gestures[0][0];
        // V3 — dedicated speech interrupt. Short Closed_Fist while TTS is speaking cancels
        // the utterance and nothing else. Runs before any other Closed_Fist semantics.
        if (isTtsSpeaking && top && top.categoryName === 'Closed_Fist' && top.score >= MIN_CONF) {
            var ts2 = ts;
            if (gestureCandidate.name !== 'Closed_Fist') {
                gestureCandidate = { name: 'Closed_Fist', since: ts2 };
            } else if (ts2 - gestureCandidate.since >= CONFIRM_HOLD_MS && !justFired('Closed_Fist', ts2)) {
                markFired('Closed_Fist', ts2);
                cancelSpeak();
                return;
            }
            return;
        }
        if (!top || top.categoryName === 'None' || top.score < MIN_CONF) {
            // trigger-hold resets only for state idle/triggering
            if (state === 'triggering') {
                setState('idle');
            }
            gestureCandidate = { name: null, since: 0 };
            return;
        }
        var name = top.categoryName;
        if (gestureCandidate.name !== name) {
            gestureCandidate = { name: name, since: ts };
        }
        var heldMs = ts - gestureCandidate.since;

        // State-specific transitions
        var trigger = (config && config.trigger_gesture) || 'Open_Palm';
        var holdMs = (config && config.hold_ms) || 1000;

        if (state === 'idle') {
            // Trigger gesture → listening
            if (name === trigger && heldMs >= holdMs) {
                markFired(name, ts);
                startListening();
                return;
            }
            if (name === trigger) {
                setState('triggering');
                return;
            }
            // Exit gesture — Closed_Fist held long → turn off
            if (name === 'Closed_Fist' && heldMs >= EXIT_HOLD_MS && !justFired(name, ts)) {
                markFired(name, ts);
                if (window.EOS_UI) EOS_UI.toast('Hands-free off', true);
                turnOff();
                return;
            }
            // V2 action gestures (Pointing_Up / Victory / ILoveYou) — registered handlers win,
            // otherwise fall back to built-in defaults.
            // V7 adds Thumb_Up / Thumb_Down as *idle-only* action gestures: they have no
            // default action (they still mean confirm/undo in other states), but pages can
            // bind them via registerGesture for app-specific idle behaviour (e.g. journal's
            // good/bad mood, publish's approve draft).
            if (heldMs >= GESTURE_ACTION_HOLD_MS && !justFired(name, ts)) {
                if (name === 'Pointing_Up' || name === 'Victory' || name === 'ILoveYou') {
                    markFired(name, ts);
                    fireGestureAction(name);
                } else if ((name === 'Thumb_Up' || name === 'Thumb_Down') && registeredGestures[name]) {
                    markFired(name, ts);
                    fireGestureAction(name);
                }
            }
            return;
        }

        if (state === 'triggering') {
            if (name === trigger && heldMs >= holdMs) {
                markFired(name, ts);
                startListening();
            } else if (name !== trigger) {
                setState('idle');
                gestureCandidate = { name: name, since: ts };
            }
            return;
        }

        if (state === 'listening') {
            if (name === 'Closed_Fist' && heldMs >= CONFIRM_HOLD_MS && !justFired(name, ts)) {
                markFired(name, ts);
                cancelListening('cancelled');
            }
            return;
        }

        if (state === 'confirm') {
            if (heldMs < CONFIRM_HOLD_MS || justFired(name, ts)) return;
            if (name === 'Thumb_Up') { markFired(name, ts); approveConfirm(); }
            else if (name === 'Thumb_Down') { markFired(name, ts); retryConfirm(); }
            else if (name === 'Closed_Fist') { markFired(name, ts); cancelConfirm(); setState('idle'); }
            return;
        }

        if (state === 'undo_window') {
            if (name === 'Thumb_Down' && heldMs >= CONFIRM_HOLD_MS && !justFired(name, ts)) {
                markFired(name, ts);
                doUndo();
            }
            return;
        }

        // V5 read-aloud navigation. Gestures during `reading` steer the queue.
        if (state === 'reading') {
            if (heldMs < CONFIRM_HOLD_MS || justFired(name, ts)) return;
            if (name === 'Pointing_Up') { markFired(name, ts); readNext(); }
            else if (name === 'Thumb_Down') { markFired(name, ts); readPrev(); }
            else if (name === 'Victory') { markFired(name, ts); readAct(); }
            else if (name === 'Closed_Fist') { markFired(name, ts); readStop('stopped'); }
            return;
        }
    }

    function markFired(name, ts) { lastGestureAction = { name: name, ts: ts }; }
    function justFired(name, ts) {
        return lastGestureAction.name === name && (ts - lastGestureAction.ts) < CONFIRM_COOLDOWN_MS;
    }

    // ──────────────────────────────────────────────────────────── V2 action gestures
    // Pages register via EOS.handsFree.registerGesture(name, fn, desc). Built-in defaults
    // only run when no handler is registered for that gesture on the current page.
    function fireGestureAction(name) {
        var reg = registeredGestures[name];
        if (reg && typeof reg.fn === 'function') {
            try { reg.fn(); } catch(e) { console.error('[hands-free] registered gesture', name, e); }
            logGestureAction(name, reg.desc || 'custom', 'registered');
            return;
        }
        // Thumb_Up / Thumb_Down have no defaults — they only fire for registered handlers
        // (otherwise they'd collide with their confirm/undo meanings).
        if (name === 'Thumb_Up' || name === 'Thumb_Down') return;
        if (name === 'Pointing_Up') {
            // V8B — finger-tip continuous scroll owns this gesture. If it's active
            // there's nothing more to do here (updateFingerScroll is already running
            // per-frame). If continuous scroll is disabled or never activated (landmarks
            // missing), fall back to the discrete one-viewport jump.
            var continuousOn = config && config.finger_scroll_enabled !== false;
            if (continuousOn && scrollState.active) {
                logGestureAction(name, 'scroll-continuous', 'default');
                return;
            }
            window.scrollBy({top: Math.floor(window.innerHeight * 0.85), behavior: 'smooth'});
            logGestureAction(name, continuousOn ? 'scroll-jump-fallback' : 'scroll-down', 'default');
            return;
        }
        if (name === 'Victory') {
            startQuickCapture();
            // Don't log here — quick-capture logs on dispatch.
            return;
        }
        if (name === 'ILoveYou') {
            if (window.EOS_UI) EOS_UI.toast('🤟 has no handler on this page', false);
            logGestureAction(name, 'unbound', 'noop');
            return;
        }
    }

    function logGestureAction(gesture, desc, outcome) {
        try {
            fetch(EOS.base + '/hands-free/api/dispatch', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    transcript: '',
                    intent: 'gesture:' + gesture,
                    target: desc || '',
                    outcome: outcome,
                }),
            }).catch(function(){});
        } catch(e) {}
    }

    function startQuickCapture() {
        cancelSpeak();
        quickCaptureMode = true;
        setState('listening');
        var provider = effectiveProvider();
        if (provider === 'os-native') {
            // os-native normally routes through the palette; for quick-capture we want
            // a throwaway invisible input so text doesn't pollute palette history.
            startOsDictateQuick();
        } else {
            startWebSpeech();
        }
    }

    function startOsDictateQuick() {
        var hidden = document.getElementById('eos-hf-quick-input');
        if (!hidden) {
            hidden = document.createElement('textarea');
            hidden.id = 'eos-hf-quick-input';
            hidden.setAttribute('aria-hidden', 'true');
            hidden.style.cssText = 'position:fixed;left:-9999px;top:0;width:2px;height:2px;opacity:0;';
            document.body.appendChild(hidden);
        }
        hidden.value = '';
        hidden.focus();
        fetch(EOS.base + '/hands-free/api/os-dictate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (!d.ok) {
                    if (window.EOS_UI) EOS_UI.toast('os-dictate: ' + (d.reason || 'failed'), false);
                    quickCaptureMode = false;
                    setState('idle');
                    return;
                }
                watchQuickInputForDictation(hidden);
            })
            .catch(function() { quickCaptureMode = false; setState('idle'); });
    }

    function watchQuickInputForDictation(input) {
        var lastChange = performance.now();
        var lastVal = input.value;
        var done = false;
        function tick() {
            if (state !== 'listening' || done) return;
            var v = input.value;
            if (v !== lastVal) { lastChange = performance.now(); lastVal = v; }
            if (v && (performance.now() - lastChange) > OS_DICTATE_SETTLE_MS) {
                done = true;
                handleTranscript(v.trim(), null);
                return;
            }
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    // ──────────────────────────────────────────────────────────── STT
    function startListening() {
        cancelSpeak();
        setState('listening');
        var provider = effectiveProvider();
        if (provider === 'os-native') {
            // Hidden input for modes where the palette would be a distraction
            // (quick-capture, ritual prompt response). Plain palette otherwise.
            if (quickCaptureMode || ritualListenMode) startOsDictateQuick();
            else startOsDictate();
        } else {
            startWebSpeech();
        }
    }

    function cancelListening(reason) {
        if (speechRec) { try { speechRec.abort(); } catch(e){} speechRec = null; }
        setState('idle');
        if (window.EOS_UI && reason) EOS_UI.toast('Hands-free: ' + reason);
    }

    function startWebSpeech() {
        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) {
            if (window.EOS_UI) EOS_UI.toast('Web Speech API unavailable in this browser', false);
            setState('idle');
            return;
        }
        var rec = new SR();
        speechRec = rec;
        rec.continuous = false;
        rec.interimResults = true;
        rec.lang = (config && config.mic_language) || navigator.language || 'en-US';
        var finalText = '';
        rec.onresult = function(e) {
            var interim = '';
            for (var i = e.resultIndex; i < e.results.length; i++) {
                if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
                else interim += e.results[i][0].transcript;
            }
            if (confirmTranscript) confirmTranscript.textContent = (finalText + interim).trim() || '…';
        };
        rec.onend = function() {
            speechRec = null;
            var text = (finalText || '').trim();
            if (!text) { setState('idle'); return; }
            handleTranscript(text);
        };
        rec.onerror = function(e) {
            speechRec = null;
            if (window.EOS_UI) EOS_UI.toast('Speech error: ' + (e.error || 'unknown'), false);
            setState('idle');
        };
        try { rec.start(); } catch(e) { setState('idle'); }
    }

    function startOsDictate() {
        // Open the command palette, focus its input, simulate Win+H, watch value for settle.
        if (EOS.keys && EOS.keys.showPalette) {
            try { EOS.keys.showPalette(); } catch(e){}
        }
        setTimeout(function() {
            var input = document.getElementById('eos-palette-input');
            if (!input) {
                if (window.EOS_UI) EOS_UI.toast('os-dictate: palette input not found', false);
                setState('idle');
                return;
            }
            input.value = '';
            input.focus();
            fetch(EOS.base + '/hands-free/api/os-dictate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (!d.ok) {
                        if (window.EOS_UI) EOS_UI.toast('os-dictate: ' + (d.reason || 'failed'), false);
                        setState('idle');
                        return;
                    }
                    watchPaletteForDictation(input);
                })
                .catch(function() {
                    if (window.EOS_UI) EOS_UI.toast('os-dictate: network error', false);
                    setState('idle');
                });
        }, 120);
    }

    function watchPaletteForDictation(input) {
        var lastChange = performance.now();
        var lastVal = input.value;
        var done = false;
        function tick() {
            if (state !== 'listening' || done) return;
            var v = input.value;
            if (v !== lastVal) {
                lastChange = performance.now();
                lastVal = v;
                // Drive palette's fuzzy filter live as dictation streams in.
                if (EOS.keys && EOS.keys._filterPalette) {
                    try { EOS.keys._filterPalette(v); } catch(e) {}
                }
            }
            if (v && (performance.now() - lastChange) > OS_DICTATE_SETTLE_MS) {
                done = true;
                var topAction = null;
                if (EOS.keys && EOS.keys._filteredActions) {
                    var filtered = EOS.keys._filteredActions() || [];
                    if (filtered.length) topAction = filtered[0];
                }
                if (EOS.keys && EOS.keys.hidePalette) EOS.keys.hidePalette();
                handleTranscript(v.trim(), topAction);
                return;
            }
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    // ──────────────────────────────────────────────────────────── TTS (voice out)
    // Mirrors the pattern in apps/assistant/pages/index.html:551-566 — strip markdown
    // noise, cap length, respect the configured rate and voice hint.
    function voiceOutMode() {
        return (config && config.voice_out) || 'confirm-only';
    }

    function selectVoice(hint) {
        if (!window.speechSynthesis) return null;
        var voices = window.speechSynthesis.getVoices() || [];
        if (!voices.length || !hint) return null;
        var h = String(hint).toLowerCase();
        for (var i = 0; i < voices.length; i++) {
            var v = voices[i];
            if ((v.name || '').toLowerCase().indexOf(h) >= 0) return v;
            if ((v.lang || '').toLowerCase().indexOf(h) >= 0) return v;
        }
        return null;
    }

    function speakText(text, opts) {
        opts = opts || {};
        if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return false;
        cancelSpeak();
        var clean = (window.EOS_UI && EOS_UI.stripMarkdownForTts)
            ? EOS_UI.stripMarkdownForTts(text)
            : String(text || '').trim();
        if (!clean) return false;
        var utt = new SpeechSynthesisUtterance(clean.substring(0, 3000));
        utt.rate = (config && Number(config.tts_rate)) || 1.1;
        var voice = selectVoice((config && config.tts_voice_hint) || '');
        if (voice) utt.voice = voice;
        utt.onstart = function() {
            isTtsSpeaking = true;
            renderChip();
            if (cheatPanel && cheatPanel.style.display !== 'none') renderCheatList();
        };
        utt.onend = function() {
            isTtsSpeaking = false;
            currentUtterance = null;
            renderChip();
            if (cheatPanel && cheatPanel.style.display !== 'none') renderCheatList();
            if (suppressNextOnEnd) { suppressNextOnEnd = false; return; }
            if (typeof opts.onEnd === 'function') {
                try { opts.onEnd(); } catch(e) { console.error('[hands-free] onEnd', e); }
            }
        };
        utt.onerror = function() {
            isTtsSpeaking = false;
            currentUtterance = null;
            renderChip();
            if (cheatPanel && cheatPanel.style.display !== 'none') renderCheatList();
            suppressNextOnEnd = false;
        };
        currentUtterance = utt;
        window.speechSynthesis.speak(utt);
        return true;
    }

    function cancelSpeak() {
        // Suppress the next onEnd so manual cancels don't trigger auto-advance or
        // other end-of-utterance side effects.
        suppressNextOnEnd = !!currentUtterance;
        if (window.speechSynthesis) {
            try { window.speechSynthesis.cancel(); } catch(e) {}
        }
        currentUtterance = null;
        if (isTtsSpeaking) { isTtsSpeaking = false; renderChip(); }
    }

    // ──────────────────────────────────────────────────────────── V5 read-aloud queue
    function startReading(source) {
        var key = String(source || '').toLowerCase().trim();
        var endpoint = READ_SOURCES[key];
        if (!endpoint) {
            if (window.EOS_UI) EOS_UI.toast('No read source: ' + source, false);
            setState('idle');
            return;
        }
        setState('reading');
        fetch(EOS.base + endpoint)
            .then(function(r) { return r.json(); })
            .then(function(d) {
                var items = (d && d.items) || [];
                if (!items.length) {
                    if (window.EOS_UI) EOS_UI.toast('Nothing to read in ' + key, false);
                    setState('idle');
                    return;
                }
                readState = {
                    source: key,
                    items: items,
                    index: 0,
                    autoAdvance: !(config && config.read_autoadvance === false),
                };
                showReadingCard();
                readCurrent();
            })
            .catch(function(err) {
                if (window.EOS_UI) EOS_UI.toast('Read failed: ' + (err && err.message || err), false);
                setState('idle');
            });
    }

    function readCurrent() {
        if (state !== 'reading') return;
        var item = readState.items[readState.index];
        if (!item) { readStop('end of queue'); return; }
        updateReadingCard(item);
        if (voiceOutMode() === 'off') {
            // Without voice-out the user is effectively browsing the queue silently
            // via gesture — still legit, just no TTS.
            return;
        }
        speakText(item.text, {
            onEnd: function() {
                if (state !== 'reading') return;
                // V7 voice-nav: after TTS, listen briefly for "next"/"back"/"stop"/"act".
                // Falls through to auto-advance if no command recognised.
                if (config && config.read_voice_nav) {
                    readVoiceNavListen();
                    return;
                }
                if (!readState.autoAdvance) return;
                setTimeout(function() {
                    if (state === 'reading') readNext();
                }, READ_AUTOADVANCE_PAUSE_MS);
            },
        });
    }

    // V7 — short listen window between read-items. Only web-speech (opening Win+H
    // between every item would be chaos). Timebound so the mic is never "always on".
    function readVoiceNavListen() {
        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) {
            // Browser doesn't support web-speech — fall through.
            if (readState.autoAdvance && state === 'reading') {
                setTimeout(function() { if (state === 'reading') readNext(); }, READ_AUTOADVANCE_PAUSE_MS);
            }
            return;
        }
        var rec;
        try { rec = new SR(); } catch(e) { return; }
        rec.continuous = false;
        rec.interimResults = false;
        rec.lang = (config && config.mic_language) || 'en-US';
        var handled = false;
        var done = false;
        var settle = function(fn) {
            if (done) return;
            done = true;
            try { rec.abort(); } catch(e) {}
            if (state !== 'reading') return;
            fn && fn();
        };
        rec.onresult = function(e) {
            handled = true;
            var text = '';
            for (var i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
            var cmd = (text || '').toLowerCase().trim();
            if (/\b(next|forward|continue|go on)\b/.test(cmd))          settle(readNext);
            else if (/\b(back|previous|again|repeat|last)\b/.test(cmd)) settle(readPrev);
            else if (/\b(stop|done|cancel|quit|end|exit)\b/.test(cmd))  settle(function() { readStop('voice stop'); });
            else if (/\b(act|do it|save|complete|yes|ok)\b/.test(cmd))  settle(readAct);
            else settle(function() {
                // Unrecognised command — fall through to auto-advance if enabled.
                if (readState.autoAdvance) readNext();
            });
        };
        rec.onend = function() {
            if (handled) return;
            settle(function() {
                if (readState.autoAdvance) readNext();
            });
        };
        rec.onerror = function() {
            settle(function() {
                if (readState.autoAdvance) readNext();
            });
        };
        try { rec.start(); } catch(e) { settle(function() {
            if (readState.autoAdvance) readNext();
        }); return; }
        // Hard time-bound: 3.5s of quiet is enough.
        setTimeout(function() { settle(function() {
            if (readState.autoAdvance) readNext();
        }); }, 3500);
    }

    function readNext() {
        if (state !== 'reading') return;
        cancelSpeak();
        if (readState.index < readState.items.length - 1) {
            readState.index++;
            readCurrent();
        } else {
            readStop('end of queue');
        }
    }

    function readPrev() {
        if (state !== 'reading') return;
        cancelSpeak();
        if (readState.index > 0) readState.index--;
        readCurrent();
    }

    function readAct() {
        if (state !== 'reading') return;
        var item = readState.items[readState.index];
        if (!item || !item.act) {
            if (window.EOS_UI) EOS_UI.toast('No action for this item', false);
            return;
        }
        var act = item.act;
        fetch(EOS.base + act.url, {
            method: act.method || 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(act.body || {}),
        })
            .then(function(r) { return r.json(); })
            .then(function() {
                if (window.EOS_UI) EOS_UI.toast((act.label || 'Acted') + ' ✓', true);
                // After acting, advance so we don't re-present the same item.
                readNext();
            })
            .catch(function(err) {
                if (window.EOS_UI) EOS_UI.toast('Act failed: ' + (err && err.message || err), false);
            });
    }

    function readStop(reason) {
        cancelSpeak();
        readState = {source: null, items: [], index: 0, autoAdvance: true};
        hideConfirmCard();
        restoreConfirmButtons();
        setState('idle');
        if (reason && window.EOS_UI) EOS_UI.toast('Read: ' + reason);
    }

    function showReadingCard() {
        if (!confirmCard) return;
        clearTimeout(confirmTimer); confirmTimer = null;
        confirmCard.style.display = '';
        confirmCard.classList.remove('hfc-qa', 'hfc-qa-ready');
        confirmCard.classList.add('hfc-reading');
        // No confirm ring in reading mode.
        confirmRing.style.transition = 'none';
        confirmRing.setAttribute('stroke-dashoffset', '113');
        setReadingButtons();
    }

    function updateReadingCard(item) {
        if (!confirmCard || !item) return;
        var total = readState.items.length;
        var pos = readState.index + 1;
        confirmState.textContent = 'Reading · ' + readState.source + ' · ' + pos + '/' + total;
        confirmIntent.textContent = item.act ? ('act: ' + item.act.label) : '(no action)';
        var body = document.createElement('div');
        body.className = 'hfc-answer';
        body.textContent = item.text || '';
        confirmTranscript.innerHTML = '';
        confirmTranscript.appendChild(body);
    }

    function setReadingButtons() {
        if (!confirmCard) return;
        var actions = confirmCard.querySelector('.hfc-actions');
        if (!actions) return;
        actions.innerHTML =
            '<button class="hfc-btn" data-act="read-prev">◀ Prev</button>' +
            '<button class="hfc-btn primary" data-act="read-act">Act</button>' +
            '<button class="hfc-btn" data-act="read-next">Next ▶</button>' +
            '<button class="hfc-btn ghost" data-act="read-stop">Stop</button>';
    }

    // ──────────────────────────────────────────────────────────── V4 proactive polling
    function startProactivePoll() {
        stopProactivePoll();
        // Initialise from "now" so we don't replay historical announcements on turn-on.
        proactiveSince = Date.now() / 1000;
        proactiveTimer = setInterval(pollProactive, PROACTIVE_POLL_MS);
    }

    function stopProactivePoll() {
        if (proactiveTimer) { clearInterval(proactiveTimer); proactiveTimer = null; }
    }

    function pollProactive() {
        if (state === 'off') return;
        fetch(EOS.base + '/hands-free/api/proactive/pending?since=' + encodeURIComponent(proactiveSince))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (!d) return;
                if (typeof d.now === 'number') proactiveSince = d.now;
                var items = d.announcements || [];
                if (!items.length) return;
                // Split rituals from plain announcements — rituals open a listening
                // session after speaking, announcements are fire-and-forget TTS.
                var ritual = null;
                var plain = [];
                items.forEach(function(a) { (a.kind === 'ritual' ? (ritual = ritual || a) : plain.push(a)); });

                // Voice-out must be enabled; otherwise drop silently (we've already
                // advanced 'since' so we won't replay later if they enable it).
                if (voiceOutMode() === 'off') return;
                // Collapse identical adjacent announcement texts.
                var lastText = null;
                plain.forEach(function(a) {
                    if (a.text && a.text !== lastText) {
                        speakText(a.text);
                        lastText = a.text;
                    }
                });
                // Only one ritual per poll. If one fires while we're mid-interaction,
                // skip — it'll be in the queue next poll.
                if (ritual && state === 'idle' && !ritualActionPending) {
                    runRitual(ritual);
                }
            })
            .catch(function() { /* network blips are expected; ignore */ });
    }

    // V6 — speak the ritual prompt, then auto-listen, then route the answer.
    function runRitual(ritual) {
        ritualActionPending = ritual.action || 'capture';
        speakText(ritual.text, {
            onEnd: function() {
                if (state !== 'idle') return;
                ritualListenMode = true;
                startListening();
            },
        });
    }

    function routeRitualResponse(action, text) {
        setState('firing');
        var url, body;
        if (action === 'journal-milestone') { url = '/journal/api/milestone'; body = {text: text}; }
        else if (action === 'task')          { url = '/task/api/add';          body = {text: text}; }
        else                                  { url = '/quick-action/api/smart-add'; body = {text: text}; }
        EOS.post(url, body)
            .then(function(res) {
                var ok = res && res.ok !== false && !res.error;
                logDispatch({
                    transcript: text,
                    intent: 'ritual:' + action,
                    target: action,
                }, ok ? 'fired' : 'failed');
                var ack = ok ? 'Saved.' : 'Save failed.';
                if (voiceOutMode() !== 'off') speakText(ack);
                else if (window.EOS_UI) EOS_UI.toast('Ritual: ' + ack, ok);
                setState('idle');
            })
            .catch(function(err) {
                if (window.EOS_UI) EOS_UI.toast('Ritual save failed: ' + (err && err.message || err), false);
                setState('idle');
            });
    }

    // ──────────────────────────────────────────────────────────── transcript pipeline
    function handleTranscript(text, paletteHit) {
        if (!text) { setState('idle'); ritualActionPending = null; ritualListenMode = false; return; }
        // V6 — ritual response short-circuit. When the system prompted with a ritual,
        // the transcript is the user's answer; route it straight to the ritual's
        // configured action. Skips resolveIntent entirely — the user wasn't issuing
        // a command.
        if (ritualActionPending) {
            var action = ritualActionPending;
            ritualActionPending = null;
            ritualListenMode = false;
            routeRitualResponse(action, text);
            return;
        }
        var wc = text.split(/\s+/).filter(Boolean).length;
        var threshold = (config && config.cleanup_threshold_words) || 8;
        if (wc > threshold) {
            setState('thinking');
            fetch(EOS.base + '/hands-free/api/cleanup', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: text}),
            })
                .then(function(r) { return r.json(); })
                .then(function(d) { resolveAndConfirm(d.cleaned || text, text, paletteHit); })
                .catch(function() { resolveAndConfirm(text, text, paletteHit); });
        } else {
            resolveAndConfirm(text, text, paletteHit);
        }
    }

    function resolveAndConfirm(text, original, paletteHit) {
        var intent = resolveIntent(text, paletteHit);
        pendingIntent = {
            transcript: text,
            original: original,
            intent: intent.label,
            target: intent.target,
            fire: intent.fire,
            undo: intent.undo || null,
            isQa: !!intent.isQa,
            question: intent.question || null,
        };
        // Q&A and Read skip the confirm loop — the answer/queue arriving is the
        // acknowledgement. Short-circuit straight to firing.
        if (intent.isQa || intent.isRead) {
            pendingIntent.isRead = !!intent.isRead;
            approveConfirm();
        } else {
            showConfirmCard();
        }
    }

    // ──────────────────────────────────────────────────────────── intent resolver
    // Priority: quick-capture override > explicit parameterized intents > palette's own top hit
    // > app fuzzy > shortcuts > no-match. Parameterized intents (capture/task/note) outrank
    // palette because "capture buy milk" shouldn't be interpreted as navigating to the capture app.
    function resolveIntent(text, paletteHit) {
        var t = (text || '').trim();
        var lower = t.toLowerCase();

        // 0. Quick-capture mode (Victory gesture entry) — entire transcript is the capture payload.
        if (quickCaptureMode) {
            quickCaptureMode = false;
            var qcPayload = t;
            return makeIntent('capture', 'capture', qcPayload,
                function() { return EOS.post('/quick-action/api/smart-add', {text: qcPayload}); });
        }

        // 1. Parameterized intents
        var captureMatch = lower.match(/^(?:capture|new capture)\s+(.+)$/);
        if (captureMatch) {
            var payload = t.replace(/^\s*(?:capture|new capture)\s+/i, '').trim();
            return makeIntent('capture', 'capture', payload,
                function() { return EOS.post('/quick-action/api/smart-add', {text: payload}); });
        }
        var taskMatch = lower.match(/^(?:new task|task)\s+(.+)$/);
        if (taskMatch) {
            var taskPayload = t.replace(/^\s*(?:new task|task)\s+/i, '').trim();
            return makeIntent('new task', 'task', taskPayload,
                function() { return EOS.post('/task/api/add', {text: taskPayload}); });
        }
        var noteMatch = lower.match(/^note\s+(.+)$/);
        if (noteMatch) {
            var notePayload = t.replace(/^\s*note\s+/i, '').trim();
            return makeIntent('note', 'capture', notePayload,
                function() { return EOS.post('/quick-action/api/add', {text: notePayload, tag: 'note'}); });
        }
        var navMatch = lower.match(/^(?:go to|open)\s+(.+)$/);
        if (navMatch) {
            var target = navMatch[1].trim();
            var appHit = fuzzyFindApp(target);
            if (appHit) {
                return makeIntent('navigate', appHit.id, appHit.name,
                    function() { window.location.href = appHit.path; return Promise.resolve({ok: true}); });
            }
        }

        // 1a. Read-aloud queue — `read <source>` opens a TTS queue the user navigates by gesture.
        var readMatch = lower.match(/^read\s+(.+)$/);
        if (readMatch) {
            var src = readMatch[1].trim().replace(/\.$/, '');
            if (READ_SOURCES[src]) {
                return {
                    label: 'read ' + src,
                    target: src,
                    isRead: true,
                    fire: function() { startReading(src); return Promise.resolve({ok: true}); },
                };
            }
            // Unknown source → surface the valid list in a toast, fall through to palette.
            if (window.EOS_UI) EOS_UI.toast('Unknown read source. Try: ' + Object.keys(READ_SOURCES).join(', '), false);
        }

        // 1b. Vault Q&A — `ask X` or any wh-question. Routes to /search/api/ask.
        //     Placed above the palette so "what did I write" doesn't navigate to an app
        //     whose name happens to match "what". V7 adds follow-up detection: short
        //     phrases like "tell me more" or "why" carry the previous Q+A as context.
        var qaQuery = null;
        var askMatch = lower.match(/^ask\s+(.+)$/);
        var followUp = /^(tell me more|what else|why|go on|continue|and then|more details?|elaborate|keep going|expand)\b/.test(lower);
        if (askMatch) {
            qaQuery = t.replace(/^\s*ask\s+/i, '').trim();
        } else if (followUp && lastQaAnswer && lastQaQuestion) {
            // Stitch previous turn into the query so the search app's LLM has context.
            qaQuery = 'Previous question: "' + lastQaQuestion +
                      '"\nPrevious answer: "' + lastQaAnswer +
                      '"\n\nFollow-up: ' + t;
        } else if (/^(what|who|when|where|why|how)\b.+/.test(lower)) {
            qaQuery = t;
        }
        if (qaQuery) {
            var q = qaQuery;
            // Display question omits the stitched context so the card stays readable;
            // the actual API call uses the full q.
            var displayQ = followUp ? t : q;
            return {
                label: 'ask vault: ' + truncate(displayQ, 60),
                target: 'vault',
                isQa: true,
                question: displayQ,
                fire: function() {
                    showAsking(displayQ);
                    return fetch(EOS.base + '/search/api/ask?q=' + encodeURIComponent(q))
                        .then(function(r) { return r.json(); })
                        .then(function(d) {
                            var answer = ((d && d.answer) || 'No answer found.').trim();
                            lastQaAnswer = answer;
                            lastQaQuestion = displayQ;
                            showAnswer(displayQ, answer);
                            if (voiceOutMode() === 'full') speakText(answer);
                            return {ok: true, answer: answer};
                        })
                        .catch(function(err) {
                            showAnswer(displayQ, 'Vault query failed: ' + (err && err.message ? err.message : 'unknown'));
                            return {ok: false, reason: String(err)};
                        });
                },
            };
        }

        // 2. Palette's own top filtered action — user sees exactly this in the open palette,
        //    so firing it removes any surprise between "what the palette showed" and "what happened".
        if (paletteHit && paletteHit.path) {
            return makeIntent('navigate', paletteHit.id, paletteHit.name || paletteHit.id,
                function() { window.location.href = paletteHit.path; return Promise.resolve({ok: true}); });
        }

        // 3. App name fuzzy (web-speech path — palette isn't live so we approximate)
        var fuzzy = fuzzyFindApp(t);
        if (fuzzy) {
            return makeIntent('navigate', fuzzy.id, fuzzy.name,
                function() { window.location.href = fuzzy.path; return Promise.resolve({ok: true}); });
        }

        // 4. Per-app registered shortcuts (search by description)
        if (EOS.keys && EOS.keys._appShortcuts) {
            var shortcuts = EOS.keys._appShortcuts;
            for (var key in shortcuts) {
                var desc = (shortcuts[key].desc || '').toLowerCase();
                if (desc && tokenOverlap(lower, desc) >= 0.6) {
                    var fn = shortcuts[key].fn;
                    var dKey = key;
                    return makeIntent('shortcut', shortcuts[key].desc, '(key ' + dKey + ')',
                        function() { try { fn(); } catch(e){} return Promise.resolve({ok: true}); });
                }
            }
        }

        // 5. No match
        return {
            label: 'no-match',
            target: '—',
            fire: function() {
                if (window.EOS_UI) EOS_UI.toast('No matching intent. Transcript logged.', false);
                return Promise.resolve({ok: false, reason: 'no_match'});
            },
        };
    }

    function makeIntent(label, target, payload, fire, undo) {
        return {label: label + (payload ? ': ' + truncate(payload, 40) : ''), target: target, fire: fire, undo: undo || null};
    }

    function truncate(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

    function fuzzyFindApp(query) {
        if (!EOS.keys || !EOS.keys._allActions) return null;
        var q = (query || '').toLowerCase().trim();
        if (!q) return null;
        var actions = EOS.keys._allActions() || [];
        var best = null;
        var bestScore = 0;
        for (var i = 0; i < actions.length; i++) {
            var a = actions[i];
            if (!a.path) continue;
            var name = (a.name || '').toLowerCase();
            var id = (a.id || '').toLowerCase();
            var score = 0;
            if (name === q || id === q) score = 1;
            else if (name.indexOf(q) === 0 || id.indexOf(q) === 0) score = 0.9;
            else if (name.indexOf(q) >= 0 || id.indexOf(q) >= 0) score = 0.7;
            else score = tokenOverlap(q, name + ' ' + id);
            if (score > bestScore) { bestScore = score; best = a; }
        }
        return (best && bestScore >= 0.6) ? best : null;
    }

    function tokenOverlap(a, b) {
        var at = a.split(/\s+/).filter(Boolean);
        var bt = b.split(/\s+/).filter(Boolean);
        if (!at.length || !bt.length) return 0;
        var hit = 0;
        for (var i = 0; i < at.length; i++) if (bt.indexOf(at[i]) >= 0) hit++;
        return hit / at.length;
    }

    // ──────────────────────────────────────────────────────────── confirm flow
    function showConfirmCard() {
        if (!pendingIntent) { setState('idle'); return; }
        restoreConfirmButtons();
        setState('confirm');
        confirmTranscript.textContent = pendingIntent.transcript;
        confirmIntent.textContent = pendingIntent.intent + ' → ' + pendingIntent.target;
        confirmState.textContent = (pendingIntent.intent === 'no-match') ? 'No match' : 'Heard';
        confirmCard.style.display = '';

        var duration = (config && config.auto_confirm_ms) || 3000;
        if (pendingIntent.intent === 'no-match') duration = Math.max(duration, 5000);
        animateRing(duration);
        if (confirmTimer) clearTimeout(confirmTimer);
        confirmTimer = setTimeout(function() {
            if (state === 'confirm') approveConfirm();
        }, duration);

        // V3 — TTS announce the intent when voice-out is enabled. Only the short label,
        // not the transcript (transcript is often redundant with the label for intents
        // like capture: "Capture: buy milk" already contains "buy milk").
        if (voiceOutMode() !== 'off' && pendingIntent.intent !== 'no-match') {
            speakText(pendingIntent.intent);
        }
    }

    function animateRing(duration) {
        var r = 18;
        var circ = 2 * Math.PI * r;
        confirmRing.setAttribute('stroke-dasharray', circ);
        confirmRing.style.transition = 'none';
        confirmRing.setAttribute('stroke-dashoffset', circ);
        // force reflow so transition applies
        void confirmRing.getBoundingClientRect();
        confirmRing.style.transition = 'stroke-dashoffset ' + duration + 'ms linear';
        confirmRing.setAttribute('stroke-dashoffset', '0');
    }

    function onConfirmClick(e) {
        var act = e.target && e.target.getAttribute('data-act');
        if (!act) return;
        if (act === 'approve') approveConfirm();
        else if (act === 'retry') retryConfirm();
        else if (act === 'cancel') { cancelConfirm(); cancelSpeak(); setState('idle'); }
        else if (act === 'reread') { if (lastQaAnswer) speakText(lastQaAnswer); }
        // V5 read card
        else if (act === 'read-next') readNext();
        else if (act === 'read-prev') readPrev();
        else if (act === 'read-act') readAct();
        else if (act === 'read-stop') readStop();
    }

    // V3 — Q&A card states. Reuses confirm card DOM, swaps content + button roles.
    function showAsking(question) {
        if (!confirmCard) return;
        clearTimeout(confirmTimer); confirmTimer = null;
        confirmCard.style.display = '';
        confirmCard.classList.add('hfc-qa');
        confirmState.textContent = 'Asking vault';
        confirmIntent.textContent = truncate(question, 80);
        confirmTranscript.innerHTML = '<div class="hfc-asking">⋯ querying…</div>';
        // No auto-confirm ring for Q&A — we're already firing.
        confirmRing.style.transition = 'none';
        confirmRing.setAttribute('stroke-dashoffset', '113');
        setQaButtons(false);
    }

    function showAnswer(question, answer) {
        if (!confirmCard) return;
        confirmCard.style.display = '';
        confirmCard.classList.add('hfc-qa', 'hfc-qa-ready');
        confirmState.textContent = 'Answer';
        confirmIntent.textContent = truncate(question, 80);
        // Answer container — scrollable, preserves paragraph breaks.
        var answerEl = document.createElement('div');
        answerEl.className = 'hfc-answer';
        answerEl.textContent = answer || '(empty answer)';
        confirmTranscript.innerHTML = '';
        confirmTranscript.appendChild(answerEl);
        setQaButtons(true);
    }

    function setQaButtons(hasAnswer) {
        if (!confirmCard) return;
        var actions = confirmCard.querySelector('.hfc-actions');
        if (!actions) return;
        var canReread = hasAnswer && !!lastQaAnswer;
        actions.innerHTML =
            (canReread ? '<button class="hfc-btn primary" data-act="reread">Re-read</button>' : '') +
            '<button class="hfc-btn ghost" data-act="cancel">Close</button>';
    }

    // Restore non-Q&A card layout so a subsequent capture/navigate reuses the original buttons.
    function restoreConfirmButtons() {
        if (!confirmCard) return;
        confirmCard.classList.remove('hfc-qa', 'hfc-qa-ready');
        var actions = confirmCard.querySelector('.hfc-actions');
        if (!actions) return;
        actions.innerHTML =
            '<button class="hfc-btn primary" data-act="approve">Approve</button>' +
            '<button class="hfc-btn" data-act="retry">Retry</button>' +
            '<button class="hfc-btn ghost" data-act="cancel">Cancel</button>';
    }

    function approveConfirm() {
        if (!pendingIntent || state === 'firing') return;
        clearTimeout(confirmTimer); confirmTimer = null;
        setState('firing');
        var p = pendingIntent;
        if (!p.isQa) confirmState.textContent = 'Firing…';
        Promise.resolve(p.fire())
            .catch(function(err) { return {ok: false, reason: String(err)}; })
            .then(function(res) {
                lastFired = p;
                logDispatch(p, (res && res.ok !== false) ? 'fired' : 'failed');
                // Q&A: leave the answer card up for the user to read / re-read; no toast,
                // no undo window. The user closes it explicitly or via Closed_Fist.
                // Read: startReading took over the state machine — don't reset it.
                if (p.isQa) {
                    pendingIntent = null;
                    setState('idle');
                    return;
                }
                if (p.isRead) {
                    pendingIntent = null;
                    // Leave state alone — startReading set it to 'reading'.
                    return;
                }
                hideConfirmCard();
                if (window.EOS_UI && p.intent !== 'no-match') EOS_UI.toast(p.intent, (res && res.ok !== false));
                pendingIntent = null;
                if (p.intent === 'no-match') { setState('idle'); return; }
                // Enter undo window
                setState('undo_window');
                if (undoTimer) clearTimeout(undoTimer);
                undoTimer = setTimeout(function() {
                    if (state === 'undo_window') setState('idle');
                    lastFired = null;
                }, 3000);
            });
    }

    function retryConfirm() {
        clearTimeout(confirmTimer); confirmTimer = null;
        pendingIntent = null;
        hideConfirmCard();
        startListening();
    }

    function cancelConfirm() {
        clearTimeout(confirmTimer); confirmTimer = null;
        pendingIntent = null;
        hideConfirmCard();
    }

    function hideConfirmCard() {
        if (!confirmCard) return;
        confirmCard.style.display = 'none';
    }

    function doUndo() {
        if (!lastFired) { setState('idle'); return; }
        var f = lastFired;
        lastFired = null;
        if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
        var p = f.undo ? Promise.resolve(f.undo()) : Promise.resolve({ok: false, reason: 'no_undo_handler'});
        p.catch(function(err) { return {ok: false, reason: String(err)}; })
         .then(function(res) {
             logDispatch(f, 'undo:' + ((res && res.ok) ? 'ok' : 'noop'));
             if (window.EOS_UI) EOS_UI.toast((res && res.ok) ? 'Undone' : 'No undo available for ' + f.target, (res && res.ok));
             setState('idle');
         });
    }

    function logDispatch(p, outcome) {
        try {
            fetch(EOS.base + '/hands-free/api/dispatch', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    transcript: p.transcript,
                    intent: p.intent,
                    target: p.target,
                    params: {},
                    outcome: outcome,
                }),
            }).catch(function(){});
        } catch(e) {}
    }

    // ──────────────────────────────────────────────────────────── public API
    // Drain any calls that arrived while the overlay was still loading (eos.js
    // installs a buffering stub so pages don't have to guard).
    var pendingQueue = (EOS.handsFree && EOS.handsFree._queue) || [];

    EOS.handsFree = {
        toggle: onChipClick,
        on: turnOn,
        off: turnOff,
        status: function() { return {state: state, config: config, supported: true, effectiveProvider: effectiveProvider()}; },
        /**
         * Register a page-specific handler for an action gesture. Overrides the built-in
         * default (scroll for Pointing_Up, quick-capture for Victory, noop for ILoveYou).
         *
         *   EOS.handsFree.registerGesture('Pointing_Up', function() {
         *       completeTopTask();
         *   }, 'Complete top task');
         *
         * Valid names: 'Pointing_Up', 'Victory', 'ILoveYou'. Re-registering replaces the
         * prior handler. Pass fn=null to clear.
         */
        registerGesture: function(gesture, fn, desc) {
            if (fn === null) { delete registeredGestures[gesture]; }
            else { registeredGestures[gesture] = {fn: fn, desc: desc || ''}; }
            if (cheatPanel && cheatPanel.style.display !== 'none') renderCheatList();
        },
        registeredGestures: function() {
            var out = {};
            for (var k in registeredGestures) out[k] = registeredGestures[k].desc || '(unnamed)';
            return out;
        },
        _speak: function(text) { return speakText(text); },
        _cancelSpeak: cancelSpeak,
        // Preview the cheat panel without turning on the camera — useful when docs
        // or the config page want to show the current gesture vocabulary.
        showCheat: function() { showCheatPanel(); },
        hideCheat: function() { hideCheatPanel(); },
    };

    // Replay any registerGesture / etc. calls buffered by the stub in eos.js.
    for (var i = 0; i < pendingQueue.length; i++) {
        var entry = pendingQueue[i];
        var method = entry[0], args = entry[1] || [];
        if (typeof EOS.handsFree[method] === 'function') {
            try { EOS.handsFree[method].apply(null, args); } catch(e) { console.error('[hands-free] replay', method, e); }
        }
    }

    // Esc globally: cancel TTS first (preempts speech), else dismiss the answer card
    // if one is open. Does NOT turn hands-free off — that's the 2s Closed_Fist gesture.
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        if (state === 'off') return;
        if (isTtsSpeaking) { cancelSpeak(); e.preventDefault(); return; }
        if (confirmCard && confirmCard.classList.contains('hfc-qa-ready')) {
            cancelConfirm();
            setState('idle');
            e.preventDefault();
        }
    });

    // ──────────────────────────────────────────────────────────── boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', buildChrome);
    } else {
        buildChrome();
    }
})();
