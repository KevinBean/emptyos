(() => {
const DEFAULT_HOST = "http://127.0.0.1:9000";

function normalizeHost(value) {
  const host = String(value || DEFAULT_HOST).trim().replace(/\/$/, "");
  const url = new URL(host);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Daemon URL must use HTTP or HTTPS");
  if ((url.pathname && url.pathname !== "/") || url.search || url.hash) throw new Error("Daemon URL must be an origin without a path");
  const loopback = ["127.0.0.1", "localhost", "[::1]", "::1"].includes(url.hostname);
  if (!loopback && url.protocol !== "https:") {
    throw new Error("Remote daemon URLs must use HTTPS/WSS");
  }
  if (!loopback && url.protocol === "http:") throw new Error("Remote daemon URLs must use HTTPS");
  return host;
}

async function restrictStorage() {
  try { await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" }); } catch (_) {}
  try { await chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" }); } catch (_) {}
}

// getConfig() runs on every fetch, and two callers poll (reading-consent every
// 1.2s, the badge on every tab event). So the read path must issue ZERO writes:
// chrome.storage.sync caps at 120 writes/min and 1800/hr, and an unconditional
// remove() here spent that budget in ~36 min of Flow reading — after which every
// sync call rejects and takes chat, capture, dictionary and the badge down with
// it. Migration is a one-shot, and only writes when a legacy token is really there.
let _migration = null;

function migrateConfig() {
  if (!_migration) _migration = _runMigration();
  return _migration;
}

async function _runMigration() {
  await restrictStorage();
  const legacy = await chrome.storage.sync.get({ token: "" });
  if (!legacy.token) return;   // nothing to migrate — never touch sync on a clean profile
  const local = await chrome.storage.local.get({ rememberToken: false, token: "" });
  const session = await chrome.storage.session.get({ token: "" });
  if (!local.token && !session.token) {
    await chrome.storage.session.set({ token: legacy.token });
  }
  await chrome.storage.sync.remove("token");
}

async function getConfig() {
  await migrateConfig();
  const synced = await chrome.storage.sync.get({ host: DEFAULT_HOST, name: "" });
  const local = await chrome.storage.local.get({ rememberToken: false, token: "" });
  const session = await chrome.storage.session.get({ token: "" });
  // A stored host that no longer passes the HTTPS floor must not throw: the options
  // page is the only surface that can repair it, and it opens by calling getConfig().
  let host = DEFAULT_HOST;
  let hostError = "";
  try { host = normalizeHost(synced.host); }
  catch (error) { hostError = error.message; }
  return {
    host, hostError, storedHost: String(synced.host || ""), name: synced.name || "",
    token: (local.rememberToken ? local.token : session.token) || "",
    rememberToken: Boolean(local.rememberToken),
  };
}

async function saveConfig({ host, name, token, rememberToken }) {
  const normalized = normalizeHost(host);
  await restrictStorage();
  await chrome.storage.sync.set({ host: normalized, name: String(name || "") });
  await chrome.storage.sync.remove("token");
  if (rememberToken) {
    await chrome.storage.local.set({ rememberToken: true, token: String(token || "") });
    await chrome.storage.session.remove("token");
  } else {
    await chrome.storage.local.set({ rememberToken: false });
    await chrome.storage.local.remove("token");
    await chrome.storage.session.set({ token: String(token || "") });
  }
}

function authHeaders(token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  return headers;
}

function wsHost(host) {
  return normalizeHost(host).replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

// ── Which apps does this daemon actually serve? ────────────────────────────
// Several surfaces (dictionary lookup + the reading layer, the job verbs) are
// backed by apps that only exist on some daemons — a public release ships
// neither. Probing lets us register only the menu items and panel sections that
// can actually work, instead of showing verbs that always fail.
//
// GET /api/apps already returns only reachable apps, so "id is in the list"
// means "its routes are live". It is NOT auth-exempt: on a private-mode daemon
// the probe needs the same bearer token every other call uses.

// null means UNKNOWN (daemon down, unconfigured, 401) — never "no apps". Every
// caller treats unknown as "show everything", so a probe failure can only ever
// restore today's behaviour, never hide a working feature.
async function fetchAvailableApps() {
  const { host, hostError, token } = await getConfig();
  if (hostError) throw new Error(hostError);
  const res = await fetch(host + "/api/apps", {
    headers: authHeaders(token),
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  const apps = await res.json();
  if (!Array.isArray(apps)) throw new Error("unexpected /api/apps shape");
  return new Set(apps.map((a) => String(a && a.id)).filter(Boolean));
}

// Cached in storage.session, not a module global: the MV3 service worker is torn
// down after ~30s idle, so a global would be empty on most wakes. Session storage
// clears on browser restart, which is exactly when onStartup re-probes.
async function getAvailableApps({ refresh = true } = {}) {
  if (refresh) {
    try {
      const ids = await fetchAvailableApps();
      await chrome.storage.session.set({ appIds: [...ids] });
      return ids;
    } catch (_) { /* fall through to the last good probe */ }
  }
  const { appIds } = await chrome.storage.session.get({ appIds: null });
  return Array.isArray(appIds) ? new Set(appIds) : null;
}

globalThis.EOS_DAEMON = {
  DEFAULT_HOST, normalizeHost, migrateConfig, getConfig, saveConfig, authHeaders, wsHost,
  fetchAvailableApps, getAvailableApps,
};
})();
