async function load() {
  const { host, token, name } = await chrome.storage.sync.get({
    host: "http://localhost:9000",
    token: "",
    name: "",
  });
  document.getElementById("host").value = host;
  document.getElementById("token").value = token;
  document.getElementById("name").value = name;
}

function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status " + (kind || "");
}

document.getElementById("save").addEventListener("click", async () => {
  const host = document.getElementById("host").value.trim().replace(/\/$/, "");
  const token = document.getElementById("token").value.trim();
  const name = document.getElementById("name").value.trim();
  await chrome.storage.sync.set({ host, token, name });
  setStatus("Saved.", "ok");
});

document.getElementById("test").addEventListener("click", async () => {
  const host = document.getElementById("host").value.trim().replace(/\/$/, "");
  const token = document.getElementById("token").value.trim();
  setStatus("Testing...");
  const headers = {};
  if (token) headers["Authorization"] = "Bearer " + token;
  try {
    const r = await fetch(host + "/api/health", { headers });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    setStatus("Connected. " + (data.status || "ok"), "ok");
  } catch (e) {
    setStatus("Failed: " + e.message, "err");
  }
});

load();
