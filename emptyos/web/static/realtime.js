/**
 * EmptyOS Realtime Client
 *
 * Usage:
 *   const eos = new EmptyOSRealtime();
 *   eos.on("vault:changed", (data) => console.log("File changed:", data.path));
 *   eos.on("task:*", (data) => console.log("Task event:", data));
 *   eos.connect();
 */
class EmptyOSRealtime {
    constructor(url) {
        this._url = url || `ws://${location.host}/ws`;
        this._ws = null;
        this._handlers = {};  // event_type -> [callbacks]
        this._reconnectDelay = 1000;
        this._maxReconnectDelay = 30000;
        this._connected = false;
    }

    connect() {
        try {
            this._ws = new WebSocket(this._url);

            this._ws.onopen = () => {
                this._connected = true;
                this._reconnectDelay = 1000;
                // Send subscriptions
                const types = Object.keys(this._handlers);
                if (types.length > 0) {
                    this._ws.send(JSON.stringify({ subscribe: types }));
                }
            };

            this._ws.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    // Server-to-browser capture requests (Web Speech API, getUserMedia)
                    // are routed separately from event-bus broadcasts. Identified by
                    // type === "capture_request" (no namespace colon, won't clash with
                    // event types like "vault:changed").
                    if (msg.type === "capture_request") {
                        this._handleCaptureRequest(msg);
                        return;
                    }
                    this._dispatch(msg);
                } catch (err) {}
            };

            this._ws.onclose = () => {
                this._connected = false;
                setTimeout(() => {
                    this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
                    this.connect();
                }, this._reconnectDelay);
            };

            this._ws.onerror = () => {
                // Suppress console noise — reconnect handles recovery
                try { this._ws.close(); } catch(e) {}
            };
        } catch (err) {
            setTimeout(() => this.connect(), this._reconnectDelay);
        }
    }

    on(eventType, callback) {
        if (!this._handlers[eventType]) {
            this._handlers[eventType] = [];
        }
        this._handlers[eventType].push(callback);

        // Update server subscription if connected
        if (this._connected && this._ws) {
            this._ws.send(JSON.stringify({ subscribe: Object.keys(this._handlers) }));
        }

        // Return unsubscribe function
        return () => {
            this._handlers[eventType] = this._handlers[eventType].filter(h => h !== callback);
            if (this._handlers[eventType].length === 0) {
                delete this._handlers[eventType];
            }
        };
    }

    _dispatch(event) {
        for (const [pattern, callbacks] of Object.entries(this._handlers)) {
            if (this._matches(event.type, pattern)) {
                callbacks.forEach(cb => {
                    try { cb(event.data, event); } catch (err) { console.error(err); }
                });
            }
        }
    }

    _matches(eventType, pattern) {
        if (pattern === eventType) return true;
        if (pattern.endsWith('*') && eventType.startsWith(pattern.slice(0, -1))) return true;
        return false;
    }

    get connected() { return this._connected; }

    // ── Browser-side capture (mic via Web Speech API; camera in v0.3.0) ──

    _handleCaptureRequest(req) {
        if (req.capability === "listen") {
            this._captureSpeech(req);
        } else if (req.capability === "see") {
            // Camera capture lands in v0.3.0; reject cleanly for now so the
            // capability chain falls through to the next provider (typically
            // human / file-upload).
            this._sendCaptureResponse(req.id, {error: "browser webcam capture not yet implemented"});
        } else {
            this._sendCaptureResponse(req.id, {error: "unknown capability " + req.capability});
        }
    }

    _captureSpeech(req) {
        const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Recognition) {
            this._sendCaptureResponse(req.id, {error: "Web Speech API not supported in this browser (try Chrome / Edge / Safari)"});
            return;
        }

        // Pause any in-page audio playback so the mic doesn't pick up TTS bleed.
        // Half-duplex per the design constraint — no AEC available in browser.
        const wasPlaying = [];
        document.querySelectorAll("audio, video").forEach(el => {
            if (!el.paused) { wasPlaying.push(el); el.pause(); }
        });

        const r = new Recognition();
        r.continuous = false;
        r.interimResults = false;
        r.lang = req.language || "en-US";

        // Visible mic indicator while capturing — keeps the user informed
        // that the browser is listening (and that the capture is happening
        // here, not on a server).
        const indicator = document.createElement("div");
        indicator.className = "eos-capture-mic";
        indicator.style.cssText = "position:fixed;bottom:20px;right:20px;background:#dc2626;color:white;padding:10px 16px;border-radius:24px;font-size:13px;z-index:10000;box-shadow:0 2px 8px rgba(0,0,0,0.2);font-family:system-ui,sans-serif;display:flex;align-items:center;gap:8px";
        indicator.innerHTML = '<span style="display:inline-block;width:8px;height:8px;background:white;border-radius:50%;animation:eos-pulse 1s ease-in-out infinite"></span>Listening' + (req.prompt ? ': ' + req.prompt : '...');
        document.body.appendChild(indicator);
        if (!document.getElementById("eos-capture-style")) {
            const s = document.createElement("style");
            s.id = "eos-capture-style";
            s.textContent = "@keyframes eos-pulse{0%,100%{opacity:1}50%{opacity:0.3}}";
            document.head.appendChild(s);
        }

        const cleanup = () => {
            indicator.remove();
            wasPlaying.forEach(el => { try { el.play(); } catch(e) {} });
        };

        r.onresult = (e) => {
            cleanup();
            const text = e.results[0][0].transcript;
            this._sendCaptureResponse(req.id, {text: text});
        };
        r.onerror = (e) => {
            cleanup();
            this._sendCaptureResponse(req.id, {error: e.error || "speech recognition failed"});
        };
        r.onend = () => {
            // If onresult/onerror didn't fire, end is the only signal we get
            cleanup();
        };

        try {
            r.start();
        } catch (err) {
            cleanup();
            this._sendCaptureResponse(req.id, {error: String(err.message || err)});
        }
    }

    _sendCaptureResponse(id, data) {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            try {
                this._ws.send(JSON.stringify({type: "capture_response", id: id, ...data}));
            } catch (err) {}
        }
    }
}
