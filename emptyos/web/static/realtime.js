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
                    const event = JSON.parse(e.data);
                    this._dispatch(event);
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
}
