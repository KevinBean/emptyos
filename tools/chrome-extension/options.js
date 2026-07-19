const { getConfig, saveConfig, authHeaders, normalizeHost } = globalThis.EOS_DAEMON;

async function load() {
  try {
    // Show the host as STORED, not as resolved — this page is the only surface that
    // can repair a host the HTTPS floor now rejects, so it has to render the broken
    // value for the user to edit rather than the default it silently fell back to.
    const { host, hostError, storedHost, token, name, rememberToken } = await getConfig();
    document.getElementById("host").value = storedHost || host;
    document.getElementById("token").value = token;
    document.getElementById("name").value = name;
    document.getElementById("remember").checked = rememberToken;
    if (hostError) setStatus(`Saved daemon URL is not usable: ${hostError}. Falling back to ${host} until you fix it.`, "err");
  } catch (error) { setStatus("Could not read settings: " + error.message, "err"); }
}

function setStatus(msg, kind) {
  const el = document.getElementById("status"); el.textContent = msg; el.className = "status " + (kind || "");
}

document.getElementById("save").addEventListener("click", async () => {
  try {
    const normalized = normalizeHost(document.getElementById("host").value);
    const daemon = new URL(normalized);
    const daemonOrigin = `${daemon.protocol}//${daemon.host}/*`;
    if (!(await chrome.permissions.contains({ origins: [daemonOrigin] }))) {
      const granted = await chrome.permissions.request({ origins: [daemonOrigin] });
      if (!granted) throw new Error("Daemon host permission was not granted");
    }
    await saveConfig({
      host: normalized,
      token: document.getElementById("token").value.trim(),
      name: document.getElementById("name").value.trim(),
      rememberToken: document.getElementById("remember").checked,
    });
    chrome.runtime.sendMessage({ type: "EOS_BROWSER_SYNC_READING" }).catch(() => {});
    // A new daemon may serve a different app set — rebuild the context menus.
    chrome.runtime.sendMessage({ type: "EOS_APPS_CHANGED" }).catch(() => {});
    setStatus("Saved. The token is not synced.", "ok");
  } catch (error) { setStatus(error.message, "err"); }
});

document.getElementById("test").addEventListener("click", async () => {
  const token = document.getElementById("token").value.trim(); setStatus("Testing...");
  try {
    const host = normalizeHost(document.getElementById("host").value);
    const response = await fetch(host + "/api/health", { headers: authHeaders(token) });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json(); setStatus("Connected. " + (data.status || "ok"), "ok");
  } catch (error) { setStatus("Failed: " + error.message, "err"); }
});

load();
