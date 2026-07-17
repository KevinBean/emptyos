(() => {
  if (globalThis.__EOS_BROWSER_BRIDGE__) return;
  globalThis.__EOS_BROWSER_BRIDGE__ = true;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const nameOf = (el) => String(el.getAttribute("aria-label") || el.innerText || el.labels?.[0]?.innerText || el.title || el.placeholder || "").trim().replace(/\s+/g, " ").slice(0, 240);
  const roleOf = (el) => el.getAttribute("role") || ({ A: "link", BUTTON: "button", INPUT: "textbox", TEXTAREA: "textbox", SELECT: "combobox" }[el.tagName] || "control");
  const sensitive = (el) => {
    const hay = [el.type, el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute("aria-label")].join(" ").toLowerCase();
    return el.type === "password" || el.type === "file" || /(password|passcode|credit|card[ _-]?(number|holder)|cvv|cvc|payment|cc-|expiry|expiration)/.test(hay);
  };
  const riskOf = (el) => {
    const text = nameOf(el).toLowerCase();
    if (sensitive(el)) return "blocked";
    if (el.type === "submit" || el.closest("form") && /(submit|send|publish|post|apply|sign|delete|remove|confirm|buy|pay|download)/.test(text)) return "consequential";
    if (el.hasAttribute("download") || /(delete|remove|send|publish|post|submit|apply|buy|pay|download|close account)/.test(text)) return "consequential";
    return "normal";
  };

  // Element-reference lifecycle: churn-surviving refs guarded per-element by an
  // identity signature. Rules + rationale live in ref-registry.js (injected just
  // before this file). registry.version() is the current snapshot generation.
  const registry = globalThis.EOS_REF_REGISTRY.createRefRegistry({
    sigOf: (el) => roleOf(el) + " " + nameOf(el),
    isConnected: (el) => el.isConnected,
    newVersion: () => crypto.randomUUID(),
  });

  function snapshot() {
    // A fresh generation: new document_version, empty ref map. Refs from the
    // previous snapshot no longer resolve, but continuous background churn (a
    // live 3D viewport, a clock) does NOT invalidate the refs in between — that
    // is enforced per-element at lookup() time, not by a document-wide observer.
    registry.reset();
    const text = String(document.body?.innerText || "").slice(0, 12000);
    const nodes = [...document.querySelectorAll("a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=true]")]
      .filter(visible).slice(0, 250);
    return {
      url: location.href, title: document.title, text,
      elements: nodes.map(el => ({ ref: registry.add(el), role: roleOf(el), name: nameOf(el), state: { disabled: Boolean(el.disabled), checked: Boolean(el.checked) }, risk: riskOf(el) })),
      truncated: String(document.body?.innerText || "").length > 12000 || nodes.length >= 250,
      document_version: registry.version(),
    };
  }

  function confirmAction(label) {
    return new Promise(resolve => {
      const host = document.createElement("div");
      host.style.cssText = "all:initial;position:fixed;inset:0;z-index:2147483647";
      const root = host.attachShadow({ mode: "closed" });
      const shade = document.createElement("div");
      shade.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);display:grid;place-items:center;font:14px system-ui";
      const card = document.createElement("div");
      card.style.cssText = "width:min(420px,calc(100vw - 32px));padding:18px;border-radius:14px;background:#171923;color:#f5f5f7;border:1px solid #3c4052;box-shadow:0 20px 70px #000";
      const title = document.createElement("strong"); title.textContent = "Allow this browser action?";
      const detail = document.createElement("p"); detail.textContent = String(label || "Consequential action").slice(0, 300);
      const buttons = document.createElement("div"); buttons.style.cssText = "display:flex;justify-content:flex-end;gap:8px";
      const deny = document.createElement("button"); deny.textContent = "Cancel";
      const allow = document.createElement("button"); allow.textContent = "Allow once";
      for (const b of [deny, allow]) b.style.cssText = "padding:8px 12px;border-radius:8px;border:1px solid #4a4f63;background:#252938;color:#fff;cursor:pointer";
      buttons.append(deny, allow); card.append(title, detail, buttons); shade.append(card); root.append(shade); document.documentElement.append(host);
      const done = value => { host.remove(); resolve(value); };
      deny.onclick = () => done(false); allow.onclick = () => done(true);
    });
  }

  async function act(args) {
    const action = args.action;
    const el = registry.lookup(args.ref, args.document_version);
    if (sensitive(el)) throw new Error("blocked_sensitive_field");
    const risk = riskOf(el);
    if (action === "click" && risk === "consequential" && !(await confirmAction(nameOf(el)))) throw new Error("confirmation_denied");
    if (action === "click") el.click();
    else if (action === "fill") {
      el.focus(); el.value = String(args.value || "").slice(0, 4000);
      el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (action === "press") {
      el.focus(); const key = String(args.key || "Enter").slice(0, 40);
      el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
    } else if (action === "select") {
      if (el.tagName !== "SELECT") throw new Error("invalid_args");
      el.value = String(args.value || ""); el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (action === "wait_for") {
      if (!visible(el)) throw new Error("stale_ref");
    } else throw new Error("unsupported_action");
    return { ok: true, confirmed: risk === "consequential" };
  }

  function present(kind, args) {
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:360px;padding:12px 14px;border-radius:10px;background:#171923;color:#f5f5f7;border:1px solid #3c4052;font:14px system-ui;box-shadow:0 8px 30px #0008";
    box.textContent = String(args.text || args.title || kind || "EmptyOS").slice(0, 800);
    document.documentElement.append(box); setTimeout(() => box.remove(), 8000);
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || !String(message.type || "").startsWith("EOS_BROWSER_")) return false;
    (async () => {
      if (message.type === "EOS_BROWSER_PING") return { ready: true };
      if (message.type === "EOS_BROWSER_SNAPSHOT") return snapshot();
      if (message.type === "EOS_BROWSER_ACTION") return act(message.args || {});
      if (message.type === "EOS_BROWSER_CONFIRM") return { approved: await confirmAction(message.label) };
      if (message.type === "EOS_BROWSER_PRESENT") return present(message.kind, message.args || {});
      throw new Error("unsupported_action");
    })().then(result => sendResponse({ ok: true, result })).catch(error => sendResponse({ ok: false, error: error.message || "browser_command_failed" }));
    return true;
  });
})();
