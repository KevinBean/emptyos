(() => {
const { getConfig, wsHost, authHeaders } = globalThis.EOS_DAEMON;

const PROTOCOL = 1;
const COMMANDS = ["list_tabs", "snapshot", "screenshot", "wait_for", "open_tab", "focus_tab", "navigate", "back", "forward", "reload", "close", "click", "fill", "press", "select", "scroll", "show_card", "show_badge", "show_toast"];
const state = { socket: null, clientId: "", sessionId: "", expiresAt: 0, tabs: new Map(), reconnects: 0, heartbeat: null, reconnectTimer: null, graceTimer: null, expiryTimer: null, readyTimer: null, pendingOrigin: "", reason: "", wanted: false };

const originPattern = value => {
  try { const u = new URL(value); return `${u.protocol}//${u.host}/*`; } catch (_) { return ""; }
};
const publicTab = tab => ({ tab_id: tab.id, url: String(tab.url || "").slice(0, 2048), title: String(tab.title || "").slice(0, 300) });

async function persist() {
  await chrome.storage.session.set({ browserSession: { sessionId: state.sessionId, expiresAt: state.expiresAt, tabs: [...state.tabs.values()], wanted: state.wanted } });
  chrome.runtime.sendMessage({ type: "EOS_BROWSER_STATE", state: status() }).catch(() => {});
}
function status() { return { armed: Boolean(state.sessionId && state.expiresAt > Date.now() / 1000), connected: state.socket?.readyState === WebSocket.OPEN, sessionId: state.sessionId, expiresAt: state.expiresAt, tabs: [...state.tabs.values()], pendingOrigin: state.pendingOrigin, reason: state.reason }; }
function scheduleExpiry() {
  clearTimeout(state.expiryTimer);
  if (state.expiresAt) state.expiryTimer = setTimeout(() => disarmLocal("expired"), Math.max(0, state.expiresAt * 1000 - Date.now()));
}
async function clientId() {
  const saved = await chrome.storage.local.get({ browserClientId: "" });
  if (saved.browserClientId) return saved.browserClientId;
  const id = crypto.randomUUID(); await chrome.storage.local.set({ browserClientId: id }); return id;
}
async function hasOrigin(url) {
  const pattern = originPattern(url); return Boolean(pattern && await chrome.permissions.contains({ origins: [pattern] }));
}
async function requestOrigins(tabs) {
  const origins = [...new Set(tabs.map(t => originPattern(t.url)).filter(Boolean))];
  for (const origin of origins) if (!(await chrome.permissions.contains({ origins: [origin] }))) return false;
  return true;
}
async function ensureBridge(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "EOS_BROWSER_PING" });
    if (!response?.ok) throw new Error("bridge_unavailable");
  }
  // ref-registry.js MUST inject before page-bridge.js — the bridge reads
  // globalThis.EOS_REF_REGISTRY at load.
  catch (_) { await chrome.scripting.executeScript({ target: { tabId }, files: ["ref-registry.js", "page-bridge.js"] }); }
}

async function connect() {
  if (!state.wanted || state.socket?.readyState === WebSocket.OPEN || state.socket?.readyState === WebSocket.CONNECTING) return;
  const { host, token } = await getConfig();
  try {
    const health = await fetch(host + "/api/health", { headers: authHeaders(token), signal: AbortSignal.timeout(2000) });
    if (health.status === 401 || health.status === 403) { await disarmLocal("authentication_failed"); return; }
  } catch (_) {}
  const ws = new WebSocket(`${wsHost(host)}/ws${token ? "?token=" + encodeURIComponent(token) : ""}`);
  state.socket = ws;
  ws.onopen = () => {
    state.reconnects = 0;
    clearTimeout(state.graceTimer);
    ws.send(JSON.stringify({ type: "browser.hello", protocol: PROTOCOL, client_id: state.clientId, extension_version: chrome.runtime.getManifest().version, commands: COMMANDS, session_id: state.sessionId }));
    clearTimeout(state.readyTimer); state.readyTimer = setTimeout(() => disarmLocal("daemon_update_required"), 5000);
    state.heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "browser.ping", protocol: PROTOCOL, session_id: state.sessionId })); }, 20000);
    persist();
  };
  ws.onmessage = event => { try { onMessage(JSON.parse(event.data)); } catch (_) {} };
  ws.onclose = () => {
    if (state.socket === ws) state.socket = null;
    clearInterval(state.heartbeat); clearTimeout(state.readyTimer); persist();
    if (!state.wanted) return;
    clearTimeout(state.graceTimer);
    state.graceTimer = setTimeout(() => disarmLocal("disconnected"), 30000);
    const delay = Math.min(1000 * 2 ** state.reconnects++, 10000);
    state.reconnectTimer = setTimeout(connect, delay);
  };
}
function send(payload) {
  if (state.socket?.readyState !== WebSocket.OPEN) throw new Error("disconnected");
  state.socket.send(JSON.stringify({ protocol: PROTOCOL, ...payload }));
}
async function onMessage(message) {
  if (message.type === "browser.ready") {
    clearTimeout(state.readyTimer);
    if (!message.enabled) { await disarmLocal(message.error || "disabled"); return; }
    if (message.session_id) { state.sessionId = message.session_id; await persist(); return; }
    if (state.sessionId) { await disarmLocal("daemon_restart"); return; }
    if (!state.sessionId && state.tabs.size) send({ type: "browser.arm", tabs: [...state.tabs.values()] });
  } else if (message.type === "browser.armed") {
    state.sessionId = message.session_id; state.expiresAt = Number(message.expires_at || 0); state.wanted = true; scheduleExpiry(); await persist();
  } else if (message.type === "browser.session_expiring") {
    state.expiresAt = Number(message.expires_at || state.expiresAt); scheduleExpiry(); await persist();
  } else if (message.type === "browser.disarmed") {
    await disarmLocal(message.reason || "daemon");
  } else if (message.type === "browser.command") {
    await dispatch(message);
  }
}
async function disarmLocal(reason) {
  state.wanted = false; state.sessionId = ""; state.expiresAt = 0; state.tabs.clear(); state.reason = reason || "";
  clearInterval(state.heartbeat); clearTimeout(state.reconnectTimer); clearTimeout(state.graceTimer); clearTimeout(state.expiryTimer); clearTimeout(state.readyTimer);
  if (state.socket) { try { state.socket.close(); } catch (_) {} state.socket = null; }
  await persist();
}
async function reply(command, ok, result, error, detail) {
  try { send({ type: "browser.result", request_id: command.request_id, session_id: state.sessionId, ok, ...(ok ? { result } : { error, ...(detail ? { detail } : {}) }) }); } catch (_) {}
}
function validatedArgs(action, raw) {
  if (!raw || Array.isArray(raw) || typeof raw !== "object") throw new Error("invalid_args");
  if (JSON.stringify(raw).length > 32000) throw new Error("payload_too_large");
  const args = { ...raw };
  if (args.url !== undefined && (typeof args.url !== "string" || args.url.length > 2048)) throw new Error("invalid_args");
  if (args.ref !== undefined && (typeof args.ref !== "string" || args.ref.length > 100)) throw new Error("invalid_args");
  if (args.document_version !== undefined && (typeof args.document_version !== "string" || args.document_version.length > 100)) throw new Error("invalid_args");
  if (["fill", "select"].includes(action) && (typeof args.value !== "string" || args.value.length > 4000)) throw new Error("invalid_args");
  if (action === "press" && (typeof args.key !== "string" || args.key.length > 40)) throw new Error("invalid_args");
  if (["show_card", "show_badge", "show_toast"].includes(action)) {
    for (const key of ["text", "title"]) if (args[key] !== undefined && (typeof args[key] !== "string" || args[key].length > 800)) throw new Error("invalid_args");
  }
  return args;
}
async function dispatch(command) {
  if (command.protocol !== PROTOCOL || command.session_id !== state.sessionId) return;
  if (Number(command.deadline || 0) < Date.now() / 1000) return reply(command, false, null, "deadline_exceeded");
  const action = String(command.action || ""); const tabId = Number(command.tab_id || 0);
  try {
    if (!COMMANDS.includes(action)) throw new Error("unsupported_action");
    const args = validatedArgs(action, command.args || {});
    let result;
    if (action === "list_tabs") result = { tabs: [...state.tabs.values()] };
    else if (action === "open_tab") {
      if (!(await hasOrigin(args.url))) throw new Error("permission_required:" + originPattern(args.url));
      const tab = await chrome.tabs.create({ url: args.url, active: false }); state.tabs.set(tab.id, publicTab(tab)); await ensureBridge(tab.id); await persist(); send({ type: "browser.tabs", session_id: state.sessionId, tabs: [...state.tabs.values()] }); result = publicTab(tab);
    } else {
      if (!state.tabs.has(tabId)) throw new Error("tab_not_shared");
      const tab = await chrome.tabs.get(tabId); if (!(await hasOrigin(tab.url))) throw new Error("permission_required:" + originPattern(tab.url));
      if (["navigate", "back", "forward", "reload"].includes(action)) {
        if (action === "navigate") {
          if (!/^https?:\/\//.test(String(args.url || ""))) throw new Error("invalid_args");
          if (!(await hasOrigin(args.url))) throw new Error("permission_required:" + originPattern(args.url));
          await chrome.tabs.update(tabId, { url: args.url });
        } else if (action === "reload") await chrome.tabs.reload(tabId);
        else await chrome.scripting.executeScript({ target: { tabId }, func: direction => history[direction](), args: [action] });
        result = { ok: true };
      } else if (action === "focus_tab") { await chrome.tabs.update(tabId, { active: true }); await chrome.windows.update(tab.windowId, { focused: true }); result = { ok: true }; }
      else if (action === "close") {
        await ensureBridge(tabId); const confirmation = await chrome.tabs.sendMessage(tabId, { type: "EOS_BROWSER_CONFIRM", label: "Close this tab" });
        if (!confirmation?.result?.approved) throw new Error("confirmation_denied"); await chrome.tabs.remove(tabId); state.tabs.delete(tabId); await persist(); result = { ok: true, confirmed: true };
      } else if (action === "screenshot") {
        await chrome.tabs.update(tabId, { active: true });
        let data_url;
        try { data_url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }); }
        catch (error) {
          if (String(error.message || error).includes("activeTab")) throw new Error("screenshot_permission_required");
          throw error;
        }
        if (data_url.length * 0.75 > 4 * 1024 * 1024) throw new Error("payload_too_large"); result = { data_url, url: tab.url, title: tab.title };
      } else if (action === "scroll") { await chrome.scripting.executeScript({ target: { tabId }, func: (x, y) => scrollBy(x, y), args: [Number(args.delta_x || 0), Number(args.delta_y || 0)] }); result = { ok: true }; }
      else if (action === "show_badge") {
        const text = String(args.text || "").slice(0, 4);
        await chrome.action.setBadgeText({ tabId, text });
        if (args.color) await chrome.action.setBadgeBackgroundColor({ tabId, color: String(args.color).slice(0, 40) });
        result = { ok: true };
      }
      else if (["show_card", "show_toast"].includes(action)) { await ensureBridge(tabId); const response = await chrome.tabs.sendMessage(tabId, { type: "EOS_BROWSER_PRESENT", kind: action, args }); if (!response?.ok) throw new Error(response?.error || "browser_command_failed"); result = response.result; }
      else {
        await ensureBridge(tabId);
        const type = action === "snapshot" ? "EOS_BROWSER_SNAPSHOT" : "EOS_BROWSER_ACTION";
        const response = await chrome.tabs.sendMessage(tabId, { type, args: { ...args, action } });
        if (!response?.ok) throw new Error(response?.error || "browser_command_failed"); result = response.result;
      }
    }
    await reply(command, true, result);
  } catch (error) {
    const code = String(error.message || "browser_command_failed");
    if (code.startsWith("permission_required:")) {
      state.pendingOrigin = code.slice("permission_required:".length); await persist();
      await reply(command, false, null, "permission_required", { origin: state.pendingOrigin });
    } else await reply(command, false, null, code);
  }
}

async function syncReadingScripts() {
  try { await chrome.scripting.unregisterContentScripts({ ids: ["eos-reading"] }); } catch (_) {}
  const grants = await chrome.permissions.getAll();
  const { host } = await getConfig();
  const daemonOrigins = new Set(["http://127.0.0.1:9000/*", "http://localhost:9000/*", originPattern(host)]);
  const matches = (grants.origins || []).filter(x => (x.startsWith("http://") || x.startsWith("https://")) && !daemonOrigins.has(x));
  if (!matches.length) return;
  await chrome.scripting.registerContentScripts([{ id: "eos-reading", matches, js: ["reading-config.js", "reading-policy.js", "reading-assist.js"], css: ["reading-assist.css"], runAt: "document_idle", persistAcrossSessions: true }]);
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!/^https?:/.test(tab.url || "") || !(await hasOrigin(tab.url))) continue;
    try { await chrome.tabs.sendMessage(tab.id, { type: "EOS_READING_REFRESH" }); }
    catch (_) {
      try {
        await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["reading-assist.css"] });
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["reading-config.js", "reading-policy.js", "reading-assist.js"] });
      } catch (_) {}
    }
  }
}

async function initBrowserSessionController() {
  await syncReadingScripts();
  state.clientId = await clientId();
  const saved = await chrome.storage.session.get({ browserSession: null });
  if (saved.browserSession?.wanted && saved.browserSession.expiresAt > Date.now() / 1000) {
    state.wanted = true; state.sessionId = saved.browserSession.sessionId || ""; state.expiresAt = saved.browserSession.expiresAt || 0;
    for (const tab of saved.browserSession.tabs || []) state.tabs.set(tab.tab_id, tab); scheduleExpiry(); connect();
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !String(message.type || "").startsWith("EOS_BROWSER_")) return false;
  (async () => {
    if (message.type === "EOS_BROWSER_STATUS") return status();
    if (message.type === "EOS_BROWSER_ARM") {
      const tabs = (message.tabs || []).filter(t => Number.isInteger(t.id) && /^https?:/.test(t.url || ""));
      if (!tabs.length) throw new Error("Select at least one tab");
      if (!(await requestOrigins(tabs))) throw new Error("Origin permission was not granted");
      await syncReadingScripts();
      state.tabs = new Map(tabs.map(t => [t.id, publicTab(t)])); state.wanted = true; state.reason = ""; state.expiresAt = Date.now() / 1000 + 3600;
      for (const tab of tabs) await ensureBridge(tab.id); await persist(); await connect();
      if (state.socket?.readyState === WebSocket.OPEN && !state.sessionId) send({ type: "browser.arm", tabs: [...state.tabs.values()] });
      return status();
    }
    if (message.type === "EOS_BROWSER_EXTEND") { send({ type: "browser.extend", session_id: state.sessionId }); return status(); }
    if (message.type === "EOS_BROWSER_DISARM") { if (state.sessionId) send({ type: "browser.disarm", session_id: state.sessionId }); await disarmLocal("user"); return status(); }
    if (message.type === "EOS_BROWSER_ALLOW_ORIGIN") { const ok = await chrome.permissions.contains({ origins: [message.origin] }); if (ok) { state.pendingOrigin = ""; await syncReadingScripts(); await persist(); } return { ok }; }
    if (message.type === "EOS_BROWSER_READING_EVERYWHERE") { const ok = await chrome.permissions.contains({ origins: ["http://*/*", "https://*/*"] }); if (ok) await syncReadingScripts(); return { ok }; }
    if (message.type === "EOS_BROWSER_SYNC_READING") { await syncReadingScripts(); return { ok: true }; }
    throw new Error("unsupported_action");
  })().then(result => sendResponse({ ok: true, result })).catch(error => sendResponse({ ok: false, error: error.message }));
  return true;
});

chrome.tabs.onRemoved.addListener(tabId => { if (state.tabs.delete(tabId)) { if (state.sessionId) { send({ type: "browser.tabs", session_id: state.sessionId, tabs: [...state.tabs.values()] }); send({ type: "browser.event", session_id: state.sessionId, event: "tab.removed" }); } persist(); } });
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (!state.tabs.has(tabId) || (!change.url && change.status !== "complete")) return;
  state.tabs.set(tabId, publicTab(tab));
  if (state.sessionId && state.socket?.readyState === WebSocket.OPEN) { send({ type: "browser.tabs", session_id: state.sessionId, tabs: [...state.tabs.values()] }); send({ type: "browser.event", session_id: state.sessionId, event: "tab.updated" }); }
  persist();
});

globalThis.EOS_BROWSER_SESSION = { init: initBrowserSessionController };
})();
