/* eos-compose.js — shared "draft a message" modal.
 *
 * One widget over the emptyos.sdk.compose engine, used by the jobs app
 * (grounded in a job note) and the people app (grounded in a person note).
 * Each host app exposes /<prefix>/api/compose/kinds, /api/compose, and
 * (optionally) /api/compose/log; this component drives all three.
 *
 * Usage:
 *   EOS_COMPOSE.open({
 *     prefix: '/jobs',
 *     ground: { job_id: 'acme--engineer' },     // merged into the compose POST body
 *     title: 'Draft a message',                  // optional
 *     logLabel: 'Log to outreach',               // optional; omit logPayload to hide Log
 *     logPayload: function(draft, sel){ return { person: '...', company: '...' }; },
 *   });
 *
 * Drafting is reversible/internal — this widget only generates + copies + logs.
 * It never sends. (v1 = copy-to-clipboard.)
 */
(function () {
  "use strict";

  var esc = (window.EOS_UI && EOS_UI.esc) || function (s) { return String(s == null ? "" : s); };
  var escAttr = (window.EOS_UI && EOS_UI.escAttr) || esc;
  function toast(m, ok) { if (window.EOS_UI && EOS_UI.toast) EOS_UI.toast(m, ok !== false); }

  var STATE = { prefix: "", channels: [], opts: null, last: null };

  function channelHasSubject(id) {
    for (var i = 0; i < STATE.channels.length; i++) {
      if (STATE.channels[i].id === id) return !!STATE.channels[i].subject;
    }
    return true;
  }

  function optionList(items, valKey, labelFn, selected) {
    return items.map(function (it) {
      var v = it[valKey];
      var sel = v === selected ? " selected" : "";
      return '<option value="' + escAttr(v) + '"' + sel + '>' + esc(labelFn(it)) + "</option>";
    }).join("");
  }

  function modalBody(meta) {
    var kinds = meta.kinds || [];
    var channels = meta.channels || [];
    var tones = meta.tones || [];
    var firstKind = kinds[0] || {};
    var defChannel = firstKind.default_channel || (channels[0] && channels[0].id) || "email";

    var recipient = (STATE.opts && STATE.opts.recipient) || "";
    return (
      '<div class="eos-compose">' +
      '<div class="eos-form-group"><label class="eos-form-label">Recipient name <span class="eos-compose-dim">(optional)</span></label>' +
        '<input id="cmp-to" class="eos-form-input" type="text" value="' + escAttr(recipient) + '" placeholder="who is this going to?"></div>' +
      '<div class="eos-form-group"><label class="eos-form-label">Message type</label>' +
        '<select id="cmp-kind" class="eos-form-input">' +
          optionList(kinds, "id", function (k) { return k.label; }, firstKind.id) +
        "</select>" +
        '<div id="cmp-kind-hint" class="eos-compose-hint">' + esc(firstKind.intent || "") + "</div>" +
      "</div>" +
      '<div class="eos-compose-row">' +
        '<div class="eos-form-group"><label class="eos-form-label">Channel</label>' +
          '<select id="cmp-channel" class="eos-form-input">' +
            optionList(channels, "id", function (c) { return c.label; }, defChannel) +
          "</select></div>" +
        '<div class="eos-form-group"><label class="eos-form-label">Tone</label>' +
          '<select id="cmp-tone" class="eos-form-input">' +
            optionList(tones, "id", function (t) { return t.id; }, "warm") +
          "</select></div>" +
      "</div>" +
      '<div class="eos-form-group"><label class="eos-form-label">Anything to add <span class="eos-compose-dim">(facts, what you want to say — optional)</span></label>' +
        '<textarea id="cmp-freeform" class="eos-form-input" rows="3" placeholder="e.g. saw their talk on grid stability; want a 15-min chat about the team"></textarea>' +
      "</div>" +
      '<div class="eos-form-actions">' +
        '<label class="eos-compose-dim" style="display:flex;align-items:center;gap:6px;margin-right:auto;">' +
          '<input type="checkbox" id="cmp-variants"> also draft a shorter variant</label>' +
        '<button class="eos-btn eos-btn-primary" id="cmp-generate">Generate draft</button>' +
      "</div>" +
      '<div id="cmp-result" class="eos-compose-result" style="display:none;"></div>' +
      "</div>"
    );
  }

  function resultHtml(draft) {
    var hasSubject = !!(draft.subject && draft.subject.length);
    var subjectRow = hasSubject
      ? '<div class="eos-form-group"><label class="eos-form-label">Subject</label>' +
        '<input id="cmp-subject" class="eos-form-input" type="text" value="' + escAttr(draft.subject) + '"></div>'
      : "";
    var why = draft.why
      ? '<div class="eos-compose-why">💡 ' + esc(draft.why) + "</div>"
      : "";
    var variant = draft.variant
      ? '<div class="eos-compose-variant"><div class="eos-compose-dim">Shorter variant — ' +
        '<a href="#" id="cmp-use-variant">use this</a></div>' +
        '<div class="eos-compose-variant-body">' + esc(draft.variant) + "</div></div>"
      : "";
    var logBtn = STATE.opts && STATE.opts.logPayload
      ? '<button class="eos-btn" id="cmp-log">' + esc((STATE.opts.logLabel) || "Log") + "</button>"
      : "";
    return (
      subjectRow +
      '<div class="eos-form-group"><label class="eos-form-label">Message</label>' +
        '<textarea id="cmp-body" class="eos-form-input" rows="9">' + esc(draft.body || "") + "</textarea></div>" +
      why +
      variant +
      '<div class="eos-form-actions" style="margin-top:10px;">' +
        '<button class="eos-btn" id="cmp-regen" style="margin-right:auto;">↻ Regenerate</button>' +
        logBtn +
        '<button class="eos-btn eos-btn-primary" id="cmp-copy">📋 Copy</button>' +
      "</div>"
    );
  }

  function fullText() {
    var subjEl = document.getElementById("cmp-subject");
    var bodyEl = document.getElementById("cmp-body");
    var body = bodyEl ? bodyEl.value : "";
    var subj = subjEl ? subjEl.value.trim() : "";
    return subj ? "Subject: " + subj + "\n\n" + body : body;
  }

  async function generate() {
    var btn = document.getElementById("cmp-generate");
    var kind = document.getElementById("cmp-kind").value;
    var channel = document.getElementById("cmp-channel").value;
    var tone = document.getElementById("cmp-tone").value;
    var freeform = document.getElementById("cmp-freeform").value.trim();
    var variants = !!document.getElementById("cmp-variants").checked;

    var toEl = document.getElementById("cmp-to");
    var toName = toEl ? toEl.value.trim() : "";
    var payload = { kind: kind, channel: channel, tone: tone, freeform: freeform, variants: variants };
    if (toName) payload.to_name = toName;
    var ground = (STATE.opts && STATE.opts.ground) || {};
    for (var k in ground) { if (Object.prototype.hasOwnProperty.call(ground, k)) payload[k] = ground[k]; }

    if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
    var resEl = document.getElementById("cmp-result");
    try {
      var r = await fetch(STATE.prefix + "/api/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var draft = await r.json();
      if (!draft || !draft.ok) {
        toast((draft && draft.error) || "Draft failed", false);
        return;
      }
      STATE.last = draft;
      resEl.innerHTML = resultHtml(draft);
      resEl.style.display = "block";
      wireResult();
    } catch (e) {
      toast("Draft failed: " + e, false);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Regenerate"; }
    }
  }

  function wireResult() {
    var copy = document.getElementById("cmp-copy");
    if (copy) copy.onclick = function () {
      var txt = fullText();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(function () { toast("Copied to clipboard", true); },
          function () { toast("Copy failed — select + copy manually", false); });
      } else {
        var ta = document.getElementById("cmp-body");
        if (ta) { ta.focus(); ta.select(); }
        toast("Select and copy (Ctrl+C)", true);
      }
    };
    var regen = document.getElementById("cmp-regen");
    if (regen) regen.onclick = generate;
    var useVar = document.getElementById("cmp-use-variant");
    if (useVar) useVar.onclick = function (e) {
      e.preventDefault();
      var bodyEl = document.getElementById("cmp-body");
      if (bodyEl && STATE.last && STATE.last.variant) bodyEl.value = STATE.last.variant;
    };
    var logBtn = document.getElementById("cmp-log");
    if (logBtn) logBtn.onclick = async function () {
      if (!STATE.opts || !STATE.opts.logPayload) return;
      var sel = {
        kind: document.getElementById("cmp-kind").value,
        channel: document.getElementById("cmp-channel").value,
        to_name: (document.getElementById("cmp-to") || {}).value || "",
        subject: (document.getElementById("cmp-subject") || {}).value || "",
        body: (document.getElementById("cmp-body") || {}).value || "",
      };
      var payload = STATE.opts.logPayload(STATE.last, sel);
      if (!payload) return;  // callback handled logging itself (e.g. opened a form)
      payload.channel = payload.channel || sel.channel;
      payload.subject = payload.subject || sel.subject;
      payload.kind = payload.kind || sel.kind;
      logBtn.disabled = true;
      try {
        var r = await fetch(STATE.prefix + "/api/compose/log", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        var res = await r.json();
        if (res && (res.ok || res.outreach)) { toast("Logged", true); logBtn.textContent = "✓ Logged"; }
        else { toast((res && res.error) || "Log failed", false); logBtn.disabled = false; }
      } catch (e) { toast("Log failed: " + e, false); logBtn.disabled = false; }
    };
  }

  function wirePicker(meta) {
    var kindSel = document.getElementById("cmp-kind");
    var chanSel = document.getElementById("cmp-channel");
    var hint = document.getElementById("cmp-kind-hint");
    var byId = {};
    (meta.kinds || []).forEach(function (k) { byId[k.id] = k; });
    if (kindSel) kindSel.onchange = function () {
      var k = byId[kindSel.value];
      if (k && hint) hint.textContent = k.intent || "";
      if (k && k.default_channel && chanSel) chanSel.value = k.default_channel;
    };
    var gen = document.getElementById("cmp-generate");
    if (gen) gen.onclick = generate;
  }

  async function open(opts) {
    opts = opts || {};
    STATE.prefix = opts.prefix || "";
    STATE.opts = opts;
    STATE.last = null;
    if (!STATE.prefix) { toast("compose: missing prefix", false); return; }
    var meta;
    try {
      meta = await fetch(STATE.prefix + "/api/compose/kinds").then(function (r) { return r.json(); });
    } catch (e) { toast("Could not load compose options", false); return; }
    STATE.channels = meta.channels || [];
    if (!window.EOS_UI || !EOS_UI.modal) { toast("EOS_UI unavailable", false); return; }
    EOS_UI.modal({ title: opts.title || "Draft a message", width: "640px", body: modalBody(meta) });
    wirePicker(meta);
  }

  window.EOS_COMPOSE = { open: open };
})();
