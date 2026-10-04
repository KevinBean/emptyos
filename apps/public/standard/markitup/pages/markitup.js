/* markitup — review list, pinned canvas, comment gutter.
 *
 * Loaded at end of body (same position the inline block would have occupied),
 * so load order against eos.js / eos-components.js is unchanged.
 *
 * Two conventions worth knowing before editing:
 *
 *  1. **No inline on* handlers anywhere.** Every interaction is delegated from
 *     a container and keyed off data- attributes. That sidesteps the
 *     inline-handler escaping trap (.claude/rules/shared-frontend.md) entirely
 *     rather than escaping around it — there is no attribute for a quote to
 *     break out of.
 *  2. **Pins are positioned in percent**, never pixels. The same markup then
 *     prints: exporting.py renders identical percent offsets into the PDF.
 *     A pixel offset measured against the on-screen image would be wrong at
 *     every other size.
 */
(function () {
  "use strict";

  var S = {
    reviews: null,       // null = not loaded yet (distinct from "none")
    rubrics: {},         // id -> {tags:[], label}
    review: null,        // open review document
    run: null,           // its pipeline state, when paused
    shot: null,          // selected shot id
    tags: {},            // tag id -> shown?
    hideResolved: false,
    selected: null,      // highlighted comment n
    hasProposal: false   // a rebuilt page exists for this review
  };

  var $ = function (id) { return document.getElementById(id); };

  // Every value interpolated into innerHTML below goes through esc(). That is
  // not boilerplate caution here: a comment's title and body are written by a
  // model from the text of a page we do not control, and a shot's url/title
  // come from that page too. This is the one surface where untrusted content
  // and markup meet, so nothing reaches innerHTML unescaped.
  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  // ── rubric helpers ────────────────────────────────────────────────

  function rubric() { return S.rubrics[(S.review && S.review.rubric) || "site"] || { tags: [] }; }

  function tagMeta(id) {
    var tags = rubric().tags || [];
    for (var i = 0; i < tags.length; i++) if (tags[i].id === id) return tags[i];
    return null;
  }

  function tagBadge(id) {
    var m = tagMeta(id);
    if (!m) return EOS_UI.statusBadge(id || "?", "");
    // statusBadge owns the class concatenation — never build it by hand.
    return EOS_UI.statusBadge(m.label, m.variant);
  }

  // ── list view ─────────────────────────────────────────────────────

  function renderList() {
    var el = $("mk-list");
    if (S.reviews === null) { el.innerHTML = '<p class="mk-muted">Loading…</p>'; return; }
    if (!S.reviews.length) {
      // emptyState returns an HTML *string* keyed on `message`, and its
      // onAction is inline-JS text — so no action is passed here; the header's
      // "+ New review" button is the affordance, and this page keeps its
      // no-inline-handler rule intact.
      el.innerHTML = EOS_UI.emptyState({
        icon: "🖍",
        message: "No reviews yet. Point it at a page and it will capture the views worth reviewing, then draft comments pinned to real elements."
      });
      return;
    }
    el.innerHTML = S.reviews.map(function (r) {
      var ready = r.status === "ready" || !r.status;
      var open = r.open ? '<span class="mk-open">' + r.open + " open</span>" : "";
      // How far triage got, as words: "3 of 40 resolved".
      var resolved = r.comments ? "<span>" + resolvedText(r.comments - (r.open || 0), r.comments) + "</span>" : "";
      // A run that has not assembled is listed too — it is usually one waiting
      // at the approval gate. It must not look like a finished review, so its
      // footer states the run status and the stage instead of counts.
      var foot = ready
        ? '<span>' + r.shots + " view" + (r.shots === 1 ? "" : "s") + "</span>" +
          "<span>" + r.comments + " comment" + (r.comments === 1 ? "" : "s") + "</span>" + open + resolved
        : '<span class="mk-open">' +
          (r.status === "paused" ? "needs you" : esc(r.status)) +
          (r.stage ? " · " + esc(r.stage) : "") + "</span>";
      return '<article class="mk-card' + (ready ? "" : " mk-card-stopped") +
        '" data-open="' + escAttr(r.id) + '">' +
        '<h3>' + esc(r.title) + "</h3>" +
        '<p class="mk-muted">' + esc(r.source || "") + "</p>" +
        "<footer>" + foot +
        '<span class="mk-when">' + esc(r.updated || r.created || "") + "</span></footer>" +
        "</article>";
    }).join("");
  }

  // ── detail: filters ───────────────────────────────────────────────

  function renderFilters() {
    var tags = rubric().tags || [];
    var counts = {};
    (S.review.comments || []).forEach(function (c) {
      counts[c.tag] = (counts[c.tag] || 0) + 1;
    });
    $("mk-filters").innerHTML =
      tags.map(function (t) {
        var on = S.tags[t.id] !== false;
        return '<button class="mk-chip' + (on ? " on" : "") + '" data-tag="' + escAttr(t.id) + '" ' +
          'title="' + escAttr(t.desc) + '">' + esc(t.label) +
          ' <em>' + (counts[t.id] || 0) + "</em></button>";
      }).join("") +
      '<button class="mk-chip mk-chip-alt' + (S.hideResolved ? " on" : "") +
      '" data-toggle="resolved">Hide resolved</button>';
  }

  function visible(c) {
    if (S.tags[c.tag] === false) return false;
    if (S.hideResolved && c.status !== "open") return false;
    return true;
  }

  // ── detail: rail, canvas, gutter ──────────────────────────────────

  function renderRail() {
    var shots = S.review.shots || [];
    $("mk-rail").innerHTML = shots.map(function (s) {
      var n = (S.review.comments || []).filter(function (c) {
        return c.shot_id === s.id && visible(c);
      }).length;
      var vp = s.viewport && s.viewport.w ? s.viewport.w + "×" + s.viewport.h : "";
      return '<button class="mk-thumb' + (s.id === S.shot ? " on" : "") + '" data-shot="' + escAttr(s.id) + '">' +
        '<img src="/markitup/api/reviews/' + encodeURIComponent(S.review.id) +
        "/shots/" + encodeURIComponent(s.image) + '" alt="" loading="lazy">' +
        '<span class="mk-thumb-t">' + esc(s.title) + "</span>" +
        '<span class="mk-thumb-m">' + esc(vp) + " · " + n + "</span>" +
        "</button>";
    }).join("");
  }

  function shotById(id) {
    var shots = S.review.shots || [];
    for (var i = 0; i < shots.length; i++) if (shots[i].id === id) return shots[i];
    return null;
  }

  function renderCanvas() {
    var shot = shotById(S.shot);
    var el = $("mk-canvas");
    if (!shot) { el.innerHTML = '<p class="mk-muted">Select a view.</p>'; return; }
    var pins = (S.review.comments || []).filter(function (c) {
      return c.shot_id === shot.id && visible(c) &&
        typeof c.x === "number" && typeof c.y === "number";
    }).map(function (c) {
      var m = tagMeta(c.tag) || {};
      return '<button class="mk-pin v-' + escAttr(m.variant || "neutral") +
        (c.n === S.selected ? " on" : "") +
        (c.anchor === "dom" ? "" : " est") +
        '" style="left:' + (c.x * 100).toFixed(3) + "%;top:" + (c.y * 100).toFixed(3) + '%"' +
        ' data-pin="' + c.n + '" title="' + escAttr(c.title) +
        (c.anchor === "dom" ? "" : " (estimated position)") + '">' + c.n + "</button>";
    }).join("");
    el.innerHTML =
      '<div class="mk-figure">' +
      '<img id="mk-img" src="/markitup/api/reviews/' + encodeURIComponent(S.review.id) +
      "/shots/" + encodeURIComponent(shot.image) + '" alt="">' + pins + "</div>";
  }

  function renderGutter() {
    var shot = shotById(S.shot);
    var rows = (S.review.comments || []).filter(function (c) {
      return shot && c.shot_id === shot.id && visible(c);
    });
    // The button is the keyboard path to a new comment (clicking the shot is
    // the pointer path, and an <img> takes no focus): a view-level comment,
    // no pin — the pin needs a point, which only a click can give.
    var head = shot
      ? '<div class="mk-gutter-head"><strong>' + esc(shot.title) + "</strong>" +
        '<p class="mk-muted">' + esc(shot.url || "") + (shot.focus ? " · " + esc(shot.focus) : "") + "</p>" +
        '<button class="eos-btn eos-btn-sm mk-add-view" data-act="add-view" ' +
        'title="A comment about this view as a whole, with no pin. Click the shot itself to pin one to an element.">' +
        "+ Comment on this view</button></div>"
      : "";
    if (!rows.length) {
      $("mk-gutter").innerHTML = head + '<p class="mk-muted mk-pad">No comments match the current filters.</p>';
      return;
    }
    $("mk-gutter").innerHTML = head + rows.map(function (c) {
      var m = tagMeta(c.tag) || {};
      // Exactly one provenance chip, never two. "estimated" belongs only to a
      // vision-placed pin; a comment with no pin at all is not an estimate of
      // anything, and showing both read as a contradiction on screen.
      var est = "";
      if (typeof c.x !== "number") {
        est = '<span class="mk-est" title="No pin — this comment is about the view as a whole">no pin</span>';
      } else if (c.anchor !== "dom") {
        est = '<span class="mk-est" title="Placed by estimate, not measured from the page">estimated</span>';
      }
      // The requirement id the pin sits on — derived from the measured anchor
      // text (shared.attach_refs / pin_to_anchor), never typed, so it is shown
      // as a fact about the pin, not as part of the comment's text.
      var ref = c.ref
        ? '<code class="mk-ref" title="Requirement id of the element this pin is on">' + esc(c.ref) + "</code>"
        : "";
      return '<article class="mk-note' + (c.n === S.selected ? " on" : "") +
        (c.status !== "open" ? " done" : "") + '" data-note="' + c.n + '">' +
        '<header><b class="mk-num v-' + escAttr(m.variant || "neutral") + '">' + c.n + "</b>" +
        tagBadge(c.tag) + ref + est +
        '<span class="mk-note-sp"></span>' +
        '<button class="mk-mini" data-act="edit" data-n="' + c.n + '" title="Edit text and tag">✎</button>' +
        '<button class="mk-mini" data-act="accept" data-n="' + c.n + '" title="Accept">✓</button>' +
        '<button class="mk-mini" data-act="dismiss" data-n="' + c.n + '" title="Dismiss">✕</button>' +
        "</header>" +
        "<h4>" + esc(c.title) + "</h4>" +
        (c.body ? "<p>" + esc(c.body) + "</p>" : "") +
        (c.status !== "open" ? '<span class="mk-state">' + esc(c.status) + "</span>" : "") +
        (c.task_sent ? '<span class="mk-state" title="Sent to tasks — ' + escAttr(c.task_sent.project || "inbox") +
          '">→ task</span>' : "") +
        "</article>";
    }).join("");
  }

  function renderAnchorHealth() {
    // A review whose comments could not be anchored is still readable prose,
    // but it is not what this app promises. Saying so — with the reason and the
    // lever — beats rendering a screenshot with no pins and letting the user
    // assume that is normal.
    var a = S.review.anchoring;
    var box = $("mk-anchor-health");
    var parts = [];
    if (a && a.comments && !(a.anchored > 0)) {
      var why = a.unresolved_ids && a.unresolved_ids.length
        ? "The model named elements that are not on the page (" +
          esc(a.unresolved_ids.join(", ")) + ")."
        : "The model did not name any element, so every comment is about the view as a whole.";
      parts.push(
        "<strong>No comment could be pinned.</strong> " + why +
        " The comments below are still usable; the pins are not. A stronger model" +
        " usually anchors most comments — switch it with the model pill above and" +
        " run the review again.");
    }
    // Shown even when nothing was kept: a run whose every comment copied the
    // prompt's example has zero comments, and that is exactly when it matters.
    var dropped = a ? Number(a.echoed_prompt) || 0 : 0;
    var cut = a ? Number(a.scrubbed_prompt_text) || 0 : 0;
    if (dropped || cut) {
      var said = [];
      if (dropped) said.push(dropped + (dropped === 1 ? " comment" : " comments") +
        " only repeated it and " + (dropped === 1 ? "was" : "were") + " dropped");
      if (cut) said.push(cut + (cut === 1 ? " comment had" : " comments had") +
        " template text cut out");
      parts.push(
        "<strong>The model copied the prompt's example.</strong> " + said.join("; ") +
        ". A stronger model usually writes its own findings — switch it with the" +
        " model pill above and run the review again.");
    }
    if (!parts.length) { box.hidden = true; box.textContent = ""; return; }
    box.hidden = false;
    box.innerHTML = parts.join("<br>");
  }

  function resolvedText(done, total) { return done + " of " + total + " resolved"; }

  // Re-run after every accept/dismiss, so the count tracks the triage.
  function renderMeta() {
    var r = S.rubrics[S.review.rubric];
    var cs = S.review.comments || [];
    var done = cs.filter(function (c) { return c.status && c.status !== "open"; }).length;
    $("mk-d-meta").textContent =
      (r ? r.label : S.review.rubric) + " · " +
      (S.review.shots || []).length + " views · " + cs.length + " comments" +
      (cs.length ? " · " + resolvedText(done, cs.length) : "") +
      (S.review.source ? " · " + S.review.source : "");
  }

  function renderDetail() {
    $("mk-d-title").textContent = S.review.title || S.review.id;
    renderMeta();
    renderAnchorHealth();
    renderSummary();
    renderProposal();
    renderFilters();
    renderRail();
    renderCanvas();
    renderGutter();
  }

  // ── summary ───────────────────────────────────────────────────────
  // The review-level argument no pin can carry: themes, the order to tackle
  // them, how the review was made. Markdown, printed first in every export.

  function renderSummary() {
    var box = $("mk-summary");
    if (!S.review) { box.hidden = true; return; }
    box.hidden = false;
    $("mk-summary-form").hidden = true;
    var body = $("mk-summary-body");
    body.hidden = false;
    var text = S.review.summary || "";
    if (text) {
      EOS_UI.paintMarkdown(body, text);
      $("mk-summary-edit").textContent = "Edit";
    } else {
      body.innerHTML = '<span class="mk-muted">No general comments yet — the themes, the order ' +
        "to tackle them, and how the review was made.</span>";
      $("mk-summary-edit").textContent = "Add comments";
    }
  }

  function editSummary() {
    if (!S.review) return;
    $("mk-summary-text").value = S.review.summary || "";
    $("mk-summary-body").hidden = true;
    $("mk-summary-form").hidden = false;
    $("mk-summary-text").focus();
  }

  async function saveSummary() {
    if (!S.review) return;
    var btn = $("mk-summary-save");
    btn.disabled = true;
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id),
      { method: "PATCH", body: JSON.stringify({ summary: $("mk-summary-text").value }) }
    );
    btn.disabled = false;
    if (!d || d.error) { EOS.toast((d && d.error) || "Save failed", "error"); return; }
    // The server's copy, not the textarea's: it strips and normalises.
    S.review.summary = d.summary;
    renderSummary();
    EOS.toast(d.summary ? "General comments saved" : "General comments cleared", "success");
  }

  $("mk-summary-edit").addEventListener("click", editSummary);
  $("mk-summary-save").addEventListener("click", saveSummary);
  $("mk-summary-cancel").addEventListener("click", renderSummary);

  // ── approval gate ─────────────────────────────────────────────────

  function renderApprove() {
    var box = $("mk-approve");
    var st = S.run;
    var paused = st && st.status === "paused" && st.stage === "discover";
    if (!paused) { box.hidden = true; box.innerHTML = ""; return; }
    var views = ((st.results || {}).discover || {}).views || [];
    box.hidden = false;
    box.innerHTML =
      "<h3>Approve the shot list</h3>" +
      '<p class="mk-muted">Nothing has been captured yet. Untick anything not worth reviewing — ' +
      "capture and comments are only paid for once you continue.</p>" +
      '<ul class="mk-views">' + views.map(function (v, i) {
        return '<li><label><input type="checkbox" checked data-view="' + i + '">' +
          "<span><strong>" + esc(v.title || v.url) + "</strong>" +
          '<em>' + esc(v.url) + (v.selector ? "  · " + esc(v.selector) : "") + "</em>" +
          (v.focus ? "<span>" + esc(v.focus) + "</span>" : "") + "</span></label></li>";
      }).join("") + "</ul>" +
      '<button class="eos-btn eos-btn-primary" id="mk-resume">Capture &amp; review these</button>';
  }

  // ── data ──────────────────────────────────────────────────────────

  async function loadList() {
    var d = await EOS.apiSafe("/markitup/api/reviews");
    S.reviews = d && d.reviews ? d.reviews : [];
    renderList();
  }

  async function openReview(id) {
    S.openId = id;
    $("mk-list-view").hidden = true;
    $("mk-detail").hidden = false;

    // Fetch BOTH before deciding. A run waiting at the approval gate has no
    // review.json yet — assemble has not run — so fetching the review first and
    // bailing on its error made the approval gate unreachable from the UI
    // entirely: the toast said "shot list ready", the page said "no such
    // review", and the only way to approve was to hand-POST /resume.
    var got = await Promise.all([
      EOS.apiSafe("/markitup/api/reviews/" + encodeURIComponent(id)),
      EOS.apiSafe("/markitup/api/run/" + encodeURIComponent(id) + "/status")
    ]);
    var d = got[0], run = got[1];

    S.review = (d && d.review) ? d.review : null;
    S.run = (run && run.run) ? run.run : null;
    S.hasProposal = !!(d && d.has_proposal);
    S.tags = {};
    S.selected = null;
    S.shot = (S.review && S.review.shots && S.review.shots.length)
      ? S.review.shots[0].id : null;

    if (!S.review && !S.run) {
      resetDetail();
      $("mk-canvas").innerHTML = EOS_UI.errorState({
        message: "Could not open that review — " +
          ((d && d.error) || "the daemon did not answer.")
      });
      return;
    }

    renderApprove();
    if (S.review) {
      renderDetail();
    } else {
      // A run that exists but has not assembled: show what it is doing, and
      // (when it is paused at discover) the approval box above.
      renderRunOnly(id);
    }
  }

  function resetDetail() {
    S.review = null; S.run = null; S.shot = null; S.selected = null; S.openId = null;
    S.hasProposal = false;
    $("mk-view-proposal").hidden = true; $("mk-rereview").hidden = true;
    $("mk-approve").hidden = true; $("mk-approve").innerHTML = "";
    $("mk-anchor-health").hidden = true;
    $("mk-exported").hidden = true; $("mk-exported").innerHTML = "";
    $("mk-summary").hidden = true;
    $("mk-filters").innerHTML = "";
    $("mk-rail").innerHTML = "";
    $("mk-gutter").innerHTML = "";
    $("mk-canvas").innerHTML = "";
    $("mk-d-title").textContent = "";
    $("mk-d-meta").textContent = "";
  }

  function renderRunOnly(id) {
    var st = S.run || {};
    var inputs = st.inputs || {};
    $("mk-d-title").textContent = inputs.title || inputs.url || id;
    var done = (st.completed || []).length;
    $("mk-d-meta").textContent =
      st.status + (st.stage ? " · " + st.stage : "") +
      " · " + done + "/5 stages" + (inputs.url ? " · " + inputs.url : "");
    $("mk-filters").innerHTML = "";
    $("mk-rail").innerHTML = "";
    $("mk-gutter").innerHTML = "";
    // A stopped run needs a way back, and it must say WHY it stopped —
    // .claude/rules/staged-pipeline.md's UI contract.
    $("mk-canvas").innerHTML = st.error
      ? EOS_UI.errorState({ message: "This run stopped at " +
          esc(st.stage || "an early stage") + " — " + esc(st.error) })
      : '<p class="mk-muted mk-pad">Nothing captured yet' +
        (st.status === "paused" ? " — approve the shot list above to continue."
                                : " (" + esc(st.status || "unknown") + ").") + "</p>";
  }

  function closeReview() {
    $("mk-detail").hidden = true;
    $("mk-list-view").hidden = false;
    resetDetail();
    loadList();
  }

  var route = EOS_UI.hashRoute({
    onShow: function (id) { openReview(id); },
    onHide: function () { closeReview(); }
  });

  // Returns whether the write landed. The pin drag below needs to know: on a
  // refusal it has to re-render to snap the pin back to the STORED position,
  // or the screen keeps showing a placement the server never accepted.
  async function patchComment(n, patch) {
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/comments/" + n,
      { method: "PATCH", body: JSON.stringify(patch) }
    );
    if (!d || d.error) {
      EOS.toast((d && d.error) || "Could not save that", "error");
      return false;
    }
    (S.review.comments || []).forEach(function (c, i) {
      if (c.n === n) S.review.comments[i] = d.comment;
    });
    renderMeta(); renderFilters(); renderRail(); renderCanvas(); renderGutter();
    return true;
  }

  function editComment(n) {
    var c = (S.review.comments || []).filter(function (x) { return x.n === n; })[0];
    if (!c) return;
    // Options must be plain strings (see newReview's note). The LABEL is the
    // safe string to pass both ways: normalize_tag accepts a display label as
    // readily as an id, and formHtml preselects by `o === val`, so sending the
    // label is what makes the current tag come up already chosen.
    var labels = (rubric().tags || []).map(function (t) { return t.label; });
    var cur = tagMeta(c.tag);
    EOS_UI.formModal({
      title: "Edit comment " + n,
      fields: [
        { key: "title", label: "Title", type: "text",
          value: c.title || "", required: true },
        { key: "body", label: "Body", type: "textarea", value: c.body || "" },
        { key: "tag", label: "Tag", type: "select", options: labels,
          value: cur ? cur.label : "" }
      ],
      onSubmit: function (v) {
        patchComment(n, { title: v.title, body: v.body, tag: v.tag });
      }
    });
  }

  // ── new review: a URL, or a document under an allow-listed root ───
  // The source select folds the document root into the kind: one option for
  // a URL, one per root the daemon allow-lists (GET /api/documents names
  // them — the page never hardcodes the roots, so the gate cannot drift from
  // the menu). The address field then means a URL or a path under that root.
  var WEB_SOURCE = "URL — a public page, or one of this daemon's own";

  async function newReview() {
    var got = await EOS.apiSafe("/markitup/api/documents");
    // A refusal still opens the dialog (a URL review needs no root), but it
    // must say so — a Source select with only URL on it would otherwise read
    // as "documents are not a thing here".
    if (!got || got.error) EOS.toast("Could not list document roots — " + ((got && got.error) || "the daemon did not answer"), "error");
    var roots = (got && got.roots) || [];
    // Suffix → source kind. The server owns the table.
    var suffixKinds = (got && got.suffix_kinds) || { ".md": "document", ".markdown": "document" };
    // Name only the kinds this daemon can produce; the suffix table above
    // covers every kind, so a PDF typed here without the extra is sent as a
    // PDF and refused with the install hint, not as a broken markdown path.
    var fileWords = ((got && got.file_kinds) || ["document"])
      .map(function (k) { return k === "document" ? "markdown" : k === "pdf" ? "PDF" : k; });
    // Option label → root key. Options are kept as plain strings and mapped
    // back here, so the submit handler never parses a label.
    var keyByLabel = {};
    var sourceOpts = [WEB_SOURCE];
    roots.forEach(function (r) {
      var label = "File — " + r.label;
      keyByLabel[label] = r.key;
      sourceOpts.push(label);
    });
    // formHtml's field vocabulary is NOT settingsPanel's, and the differences
    // all fail silently:
    //   - the boolean type is `checkbox`; "boolean" is not in formHtml's type
    //     list, so it falls through to a TEXT input — and any keystroke in a
    //     box labelled "skip the approval gate" would then arm the spend gate.
    //   - `hint` is not rendered at all, so guidance has to live in the label.
    // Verified against eos-components.js formHtml/formValues, not assumed.
    EOS_UI.formModal({
      title: "New review",
      submitLabel: "Start",
      fields: [
        { key: "source", label: "Source", type: "select", options: sourceOpts,
          value: WEB_SOURCE, autofocus: false },
        { key: "ref", type: "text", required: true, autofocus: true,
          label: "Address — the URL, or for a file (" + fileWords.join(", ") + ") its path under the root (e.g. spec/05-system-requirements.md)" },
        { key: "title", label: "Title (optional — defaults to the page or document title)",
          type: "text" },
        { key: "rubric", label: "Rubric — site: someone else's page · design: one of ours · requirements: a rendered spec (a URL gives it headings to pin to, a document gives it requirement rows)",
          type: "select", options: Object.keys(S.rubrics) },
        { key: "approve", type: "checkbox",
          label: "Skip the approval gate (a URL review captures and spends immediately; a document review always goes straight through)" }
      ],
      onSubmit: async function (v) {
        var key = keyByLabel[v.source];
        var ref = String(v.ref || "").trim();
        var body = { title: v.title, rubric: v.rubric };
        if (key) {
          // One file source; its ref is <root>/<path> and its kind follows the
          // suffix. The daemon's gate decides whether the path is acceptable —
          // nothing is checked here that the server would not check again.
          var dot = ref.lastIndexOf(".");
          var kind = (dot >= 0 && suffixKinds[ref.slice(dot).toLowerCase()]) || "document";
          body.sources = [{ kind: kind, title: v.title,
                            ref: key + "/" + ref.replace(/^\/+/, "") }];
        } else {
          body.url = ref;
          body.approve = v.approve;
        }
        var d = await EOS.apiSafe("/markitup/api/run", {
          method: "POST", body: JSON.stringify(body)
        });
        if (!d || d.error) { EOS.toast((d && d.error) || "Could not start", "error"); return; }
        EOS.toast(d.status === "paused" ? "Shot list ready for approval" : "Review complete", "success");
        await loadList();
        if (d.run_id) route.set(d.run_id);
      }
    });
    wireDocumentPicker(keyByLabel);
  }

  // After the modal is in the DOM: a datalist of markdown files under the
  // chosen root, refetched as the user types (the daemon filters by
  // substring and caps the page), and a placeholder that says what the
  // address field means right now. A URL source gets no list at all.
  // Refetch delay while typing. Below ~200 ms a list that lags the keystroke
  // still reads as live; a vault walk measured 6–370 ms on 15k notes, so a
  // shorter wait would mostly queue answers the `seq` guard then discards.
  var DOC_LIST_DEBOUNCE_MS = 180;

  function wireDocumentPicker(keyByLabel) {
    var sel = $("eos-form-source"), inp = $("eos-form-ref");
    if (!sel || !inp) return;
    var list = document.createElement("datalist");
    list.id = "mk-doc-list";
    inp.parentNode.appendChild(list);
    var timer = null, seq = 0;
    async function refresh() {
      var key = keyByLabel[sel.value];
      if (!key) {
        inp.removeAttribute("list"); list.innerHTML = "";
        inp.placeholder = "https://…";
        return;
      }
      inp.setAttribute("list", list.id);
      inp.placeholder = "path under " + sel.value.replace(/^File — /, "");
      var mine = ++seq;
      var d = await EOS.apiSafe("/markitup/api/documents?root=" + encodeURIComponent(key) +
                                "&q=" + encodeURIComponent(inp.value.trim()));
      if (mine !== seq) return;   // a newer keystroke's answer wins
      list.innerHTML = ((d && d.files) || []).map(function (f) {
        return '<option value="' + escAttr(f) + '">';
      }).join("");
    }
    sel.addEventListener("change", refresh);
    inp.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(refresh, DOC_LIST_DEBOUNCE_MS);
    });
    refresh();
  }

  // ── click-to-comment ──────────────────────────────────────────────
  // A click on the shot (not on a pin) asks the daemon which measured element
  // that point snaps to, shows the answer in the dialog BEFORE the user types,
  // and then adds the comment with `snap: true` so the server pins it to that
  // same element — `anchor: dom`, `el` and `ref` recorded. When nothing is in
  // reach the comment is added with no pin at all; this path never produces
  // an estimated position.
  // `pt` is the normalised click point, or null for the keyboard path (the
  // gutter's "+ Comment on this view" button): then there is no snap and the
  // comment is about the view as a whole.
  async function commentAt(pt) {
    // Bound at entry. The snap fetch below yields, and a rail click or Back
    // during it moves S.shot / S.review — the comment must land on the shot
    // the user clicked, not whatever is current when the dialog submits.
    var review = S.review, shot = S.shot;
    if (!review || !shot) return;
    var base = "/markitup/api/reviews/" + encodeURIComponent(review.id);
    var s = { el: "" };
    if (pt) {
      s = await EOS.apiSafe(base + "/shots/" + encodeURIComponent(shot) +
                            "/snap?x=" + pt.x.toFixed(5) + "&y=" + pt.y.toFixed(5));
      if (!s || s.error) { EOS.toast((s && s.error) || "Could not read that view's anchors", "error"); return; }
    }
    var pinned = s.el
      ? (s.ref ? s.ref + " · " : "") + (s.tag ? "<" + s.tag + "> " : "") + (s.text || s.el)
      : "No pin — " + (pt ? (s.reason || "nothing measured here") : "added from the keyboard") +
        ". This comment will be about the view as a whole.";
    var labels = (rubric().tags || []).map(function (t) { return t.label; });
    EOS_UI.formModal({
      title: "New comment",
      submitLabel: "Add comment",
      fields: [
        // `formula` is the one never-submitted field type; its disabled
        // <input> is swapped for a paragraph below, since a sentence this
        // long clips in a single-line control and cannot be read to the end.
        { key: "pinned", label: s.el ? "Pinned to (measured element)" : "Pin",
          type: "formula", value: pinned, autofocus: false },
        { key: "title", label: "Title", type: "text", required: true, autofocus: true },
        { key: "body", label: "Body", type: "textarea" },
        { key: "tag", label: "Tag", type: "select", options: labels }
      ],
      onSubmit: async function (v) {
        var payload = { shot_id: shot, title: v.title, body: v.body, tag: v.tag };
        if (pt) { payload.x = pt.x; payload.y = pt.y; payload.snap = true; }
        var d = await EOS.apiSafe(base + "/comments", {
          method: "POST", body: JSON.stringify(payload)
        });
        if (!d || d.error) { EOS.toast((d && d.error) || "Could not add that", "error"); return; }
        (review.comments = review.comments || []).push(d.comment);
        if (S.review !== review) return;   // navigated away meanwhile — saved, not shown
        S.selected = d.comment.n;
        renderMeta(); renderFilters(); renderRail(); renderCanvas(); renderGutter();
        EOS.toast(!pt ? "Added as a comment on the view"
          : d.snapped ? "Pinned to " + (d.comment.ref || d.comment.el)
          : "Added without a pin — " + (d.reason || "nothing measured there"), "success");
      }
    });
    var fact = $("eos-form-pinned");
    if (fact) {
      var p = document.createElement("p");
      p.className = "mk-pinned";
      p.textContent = pinned;
      fact.replaceWith(p);   // formValues skips a `formula` key whose element is gone
    }
  }

  // ── events (delegated — no inline handlers anywhere) ──────────────

  $("mk-new").addEventListener("click", newReview);
  $("mk-proposal-file").addEventListener("change", async function (e) {
    var f = e.target.files && e.target.files[0];
    e.target.value = "";   // so re-picking the same file fires again
    await uploadProposal(f);
  });
  $("mk-back").addEventListener("click", function () { route.clear(); });

  $("mk-list").addEventListener("click", function (e) {
    var card = e.target.closest("[data-open]");
    if (card) route.set(card.getAttribute("data-open"));
  });

  $("mk-filters").addEventListener("click", function (e) {
    var chip = e.target.closest("[data-tag]");
    if (chip) {
      var t = chip.getAttribute("data-tag");
      S.tags[t] = S.tags[t] === false;
      renderFilters(); renderRail(); renderCanvas(); renderGutter();
      return;
    }
    if (e.target.closest('[data-toggle="resolved"]')) {
      S.hideResolved = !S.hideResolved;
      renderFilters(); renderRail(); renderCanvas(); renderGutter();
    }
  });

  $("mk-rail").addEventListener("click", function (e) {
    var b = e.target.closest("[data-shot]");
    if (!b) return;
    S.shot = b.getAttribute("data-shot");
    S.selected = null;
    renderRail(); renderCanvas(); renderGutter();
  });

  // ── pin selection + drag ──────────────────────────────────────────
  // A pointerup always emits a click too, so a finished drag would ALSO run
  // the select handler below and scroll the gutter out from under the user.
  // The flag is consumed by exactly one click, never left armed.
  var suppressClick = false;
  var drag = null;
  var press = null;       // a press on the shot itself (not a pin): where it began
  var DRAG_SLOP_PX = 4;   // under this, a press-and-release is a click

  $("mk-canvas").addEventListener("click", function (e) {
    if (suppressClick) { suppressClick = false; return; }
    var pin = e.target.closest("[data-pin]");
    if (!pin) {
      // Empty canvas: a new comment at that point. Only on the figure itself
      // — the "Select a view" placeholder is not a surface to pin to.
      var fig = e.target.closest(".mk-figure");
      if (!fig || !S.review || !S.shot) return;
      var pt = figurePoint(e, fig);
      if (pt) commentAt(pt);
      return;
    }
    S.selected = parseInt(pin.getAttribute("data-pin"), 10);
    renderCanvas(); renderGutter();
    var note = document.querySelector('.mk-note[data-note="' + S.selected + '"]');
    if (note) note.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  // Normalised against the FIGURE's rendered box — convention 2 at the top of
  // this file. The stored x/y are percentages of the captured region, and
  // exporting.py prints those same percentages, so a drop measured in pixels
  // off the on-screen image would land somewhere else in the PDF.
  function figurePoint(e, fig) {
    var r = fig.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))
    };
  }

  $("mk-canvas").addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    var pin = e.target.closest("[data-pin]");
    var fig = pin && pin.closest(".mk-figure");
    if (!fig) {
      // A mouse press-move-release on the shot (a scroll-drag, a selection
      // attempt) still dispatches a click; remember where it began so endDrag
      // can tell it from a click meant to place a comment.
      if (e.target.closest(".mk-figure")) press = { x0: e.clientX, y0: e.clientY };
      return;
    }
    drag = { n: parseInt(pin.getAttribute("data-pin"), 10), pin: pin, fig: fig,
             moved: false, x0: e.clientX, y0: e.clientY, pt: null };
    // Capture on the pin, so a fast drag that outruns the 22px button still
    // reports moves instead of stranding the pin mid-flight.
    try { pin.setPointerCapture(e.pointerId); } catch (err) { /* non-fatal */ }
    e.preventDefault();   // suppress native drag-image + text selection
  });

  $("mk-canvas").addEventListener("pointermove", function (e) {
    if (!drag) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x0) < DRAG_SLOP_PX
                    && Math.abs(e.clientY - drag.y0) < DRAG_SLOP_PX) return;
    var pt = figurePoint(e, drag.fig);
    if (!pt) return;
    drag.moved = true;
    drag.pt = pt;
    drag.pin.classList.add("dragging");
    drag.pin.style.left = (pt.x * 100).toFixed(3) + "%";
    drag.pin.style.top = (pt.y * 100).toFixed(3) + "%";
  });

  async function endDrag(e) {
    if (press) {
      var p = press;
      press = null;
      // Only a pointerup is followed by a click; arming the flag on a
      // pointercancel would swallow the NEXT real click instead.
      if (e.type === "pointerup" &&
          (Math.abs(e.clientX - p.x0) >= DRAG_SLOP_PX || Math.abs(e.clientY - p.y0) >= DRAG_SLOP_PX)) {
        suppressClick = true;
      }
    }
    var d = drag;
    if (!d) return;
    drag = null;
    try { d.pin.releasePointerCapture(e.pointerId); } catch (err) { /* gone */ }
    d.pin.classList.remove("dragging");
    if (!d.moved || !d.pt) return;   // never moved — let the click select it
    suppressClick = true;
    // The server marks a hand-moved pin `anchor: human`, so the re-render
    // inside patchComment is what flips the chip from measured to estimated.
    var ok = await patchComment(d.n, { x: d.pt.x, y: d.pt.y });
    if (!ok) renderCanvas();   // refused — snap back to the stored position
  }

  $("mk-canvas").addEventListener("pointerup", endDrag);
  $("mk-canvas").addEventListener("pointercancel", endDrag);

  $("mk-gutter").addEventListener("click", function (e) {
    var act = e.target.closest("[data-act]");
    if (act) {
      var kind = act.getAttribute("data-act");
      if (kind === "add-view") { commentAt(null); return; }   // no data-n to parse
      var n = parseInt(act.getAttribute("data-n"), 10);
      // Branch edit out FIRST. The status ternary below is "accept or else
      // dismiss", so any third action falling into it would silently dismiss
      // the comment it was meant to open for editing.
      if (kind === "edit") { editComment(n); return; }
      var want = kind === "accept" ? "accepted" : "dismissed";
      var cur = (S.review.comments || []).filter(function (c) { return c.n === n; })[0];
      patchComment(n, { status: cur && cur.status === want ? "open" : want });
      return;
    }
    var note = e.target.closest("[data-note]");
    if (note) {
      S.selected = parseInt(note.getAttribute("data-note"), 10);
      renderCanvas(); renderGutter();
    }
  });

  $("mk-approve").addEventListener("click", async function (e) {
    if (!e.target.closest("#mk-resume") || !S.review || !S.run) return;
    var keep = [];
    var views = ((S.run.results || {}).discover || {}).views || [];
    document.querySelectorAll("#mk-approve [data-view]").forEach(function (cb) {
      if (cb.checked) keep.push(views[parseInt(cb.getAttribute("data-view"), 10)]);
    });
    if (!keep.length) { EOS.toast("Pick at least one view", "error"); return; }
    e.target.disabled = true;
    e.target.textContent = "Capturing…";
    var d = await EOS.apiSafe(
      "/markitup/api/run/" + encodeURIComponent(S.review.id) + "/resume",
      { method: "POST", body: JSON.stringify({ views: keep }) }
    );
    if (!d || d.error) { EOS.toast((d && d.error) || "Run failed", "error"); }
    openReview(S.review.id);
  });

  // A proposal is a rebuilt version of the reviewed page. Serving it from this
  // daemon is what lets it be re-reviewed at all — capture refuses file:// and
  // refuses any non-public host that is not us — so the loop is upload, then
  // re-review, then compare the two reviews.
  function renderProposal() {
    var id = S.review && S.review.id;
    var link = $("mk-view-proposal");
    var again = $("mk-rereview");
    var url = id ? "/markitup/api/reviews/" + encodeURIComponent(id) + "/proposal" : "";
    link.hidden = again.hidden = !(id && S.hasProposal);
    if (url) link.href = url;
  }

  async function uploadProposal(file) {
    if (!file || !S.review) return;
    var html = await file.text();
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/proposal",
      { method: "POST", body: JSON.stringify({ html: html }) }
    );
    if (!d || d.error) { EOS.toast((d && d.error) || "Upload failed", "error"); return; }
    S.hasProposal = true;
    renderProposal();
    EOS.toast("Proposal saved (" + d.bytes + " bytes)", "success");
  }

  document.querySelector(".mk-detail-actions").addEventListener("click", async function (e) {
    var act = e.target.closest("[data-act]");
    // First, and outside the S.review gate: a paused run has no review yet but
    // can still be deleted — and everything else below falls through to
    // re-review, so Delete must never reach it.
    if (act && act.getAttribute("data-act") === "delete-review") { deleteReview(); return; }
    if (act && act.getAttribute("data-act") === "send-tasks") { sendToTasks(); return; }
    if (act && S.review) {
      if (act.getAttribute("data-act") === "upload") {
        $("mk-proposal-file").click();
        return;
      }
      // Re-review: the URL is built server-side, because a browser reaching the
      // daemon through a tailnet name would send an origin that capture's own
      // host+port check rejects even though it is us.
      act.disabled = true;
      var r = await EOS.apiSafe(
        "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/rereview",
        { method: "POST", body: "{}" }
      );
      act.disabled = false;
      if (!r || r.error) { EOS.toast((r && r.error) || "Could not start", "error"); return; }
      EOS.toast("Re-review complete", "success");
      await loadList();
      if (r.run_id) route.set(r.run_id);
      return;
    }
    var b = e.target.closest("[data-export]");
    if (!b || !S.review) return;
    b.disabled = true;
    var d = await EOS.apiSafe(
      "/markitup/api/reviews/" + encodeURIComponent(S.review.id) + "/export",
      { method: "POST", body: JSON.stringify({ format: b.getAttribute("data-export") }) }
    );
    b.disabled = false;
    if (!d || d.error) { EOS.toast((d && d.error) || "Export failed", "error"); return; }
    EOS.toast("Exported " + (d.format === "pdf" ? "PDF" : "markdown"), "success");
    showExported(d);
  });

  // The written file as links, not a path in a toast. Delegated data-
  // attributes, per this file's no-inline-handler convention — which is also
  // why EOS.noteActions (inline onclick) is not used here.
  function showExported(d) {
    var box = $("mk-exported");
    var p = d.vault_path || "";
    var name = (p || d.path || "").split(/[\\/]/).pop();
    box.hidden = false;
    if (!p) { box.textContent = "Exported to " + (d.path || ""); return; }
    box.innerHTML = "Exported: " +
      (d.format === "pdf" ? esc(name)
        : '<a href="#" data-view-note="' + escAttr(p) + '">' + esc(name) + "</a>") +
      ' · <a href="#" data-open-external="' + escAttr(p) + '">open externally</a>';
  }

  $("mk-exported").addEventListener("click", function (e) {
    var v = e.target.closest("[data-view-note]");
    var x = e.target.closest("[data-open-external]");
    if (!v && !x) return;
    e.preventDefault();
    if (v) EOS.viewNote(v.getAttribute("data-view-note"));
    else EOS.openInViewer(x.getAttribute("data-open-external"));
  });

  // ── accepted comments → tasks: preview, then create ───────────────
  // The server builds the task text and refuses comments already sent; this
  // only shows the plan and posts the confirmation.
  async function sendToTasks() {
    if (!S.review) return;
    var id = S.review.id;
    var base = "/markitup/api/reviews/" + encodeURIComponent(id) + "/tasks";
    var plan = await EOS.apiSafe(base, { method: "POST", body: JSON.stringify({ dry_run: true }) });
    if (!plan || plan.error) { EOS.toast((plan && plan.error) || "Could not prepare the tasks", "error"); return; }
    var tasks = plan.tasks || [];
    if (!tasks.length) {
      var sent = (plan.skipped || []).filter(function (s) { return s.reason === "already sent"; }).length;
      EOS.toast(sent ? "Every accepted comment is already a task" : "Accept a comment first — only accepted comments are sent", "info");
      return;
    }
    var projects = await EOS.apiSafe("/projects/api/projects");
    // /projects/api/projects answers a bare array of project dicts.
    var list = Array.isArray(projects) ? projects : ((projects && projects.projects) || []);
    var ids = list.map(function (p) { return p.id || p.name; }).filter(Boolean);
    var known = ids.map(function (p) { return String(p).toLowerCase(); });   // projects matches case-insensitively
    var body =
      '<p class="mk-muted">' + tasks.length + " task" + (tasks.length === 1 ? "" : "s") +
      " will be created. Each comment is sent once; it is marked “→ task” afterwards.</p>" +
      '<ul class="mk-task-plan">' + tasks.map(function (t) {
        return "<li>" + esc(t.text) + "</li>";
      }).join("") + "</ul>" +
      '<label class="mk-muted" for="mk-task-project">Project (blank = inbox)</label>' +
      '<input id="mk-task-project" class="eos-input" list="mk-task-projects" placeholder="inbox">' +
      '<datalist id="mk-task-projects">' + ids.map(function (p) {
        return '<option value="' + escAttr(p) + '">';
      }).join("") + "</datalist>" +
      // The projects app creates any project it is handed, so a typo would
      // quietly start a new one — say so before the click, not after.
      '<p class="mk-muted" id="mk-task-newproj" hidden>New project — it will be created.</p>';
    EOS_UI.modal({
      title: "Send accepted comments to tasks",
      body: body,
      actions: [{
        label: "Create " + tasks.length + " task" + (tasks.length === 1 ? "" : "s"), primary: true,
        onClick: async function () {
          var project = ($("mk-task-project") && $("mk-task-project").value.trim()) || "";
          var d = await EOS.apiSafe(base, { method: "POST", body: JSON.stringify({
            dry_run: false, project: project, comments: tasks.map(function (t) { return t.n; }) }) });
          if (!d || d.error) {
            EOS.toast((d && d.error) || "Could not create the tasks", "error");
            // Some landed: the plan is stale, close and show the marks. None
            // did (a refused project name): stay open so it can be corrected.
            if (d && d.sent && d.sent.length) { await openReview(id); return true; }
            return false;
          }
          EOS.toast("Created " + d.sent.length + " task" + (d.sent.length === 1 ? "" : "s") +
                    (project ? " in " + project : " in the inbox"), "success");
          await openReview(id);
          return true;
        }
      }]
    });
    var input = $("mk-task-project");
    if (input) input.addEventListener("input", function () {
      var v = input.value.trim();
      $("mk-task-newproj").hidden = !v || known.indexOf(v.toLowerCase()) !== -1;
    });
  }

  // Works on a run that has not assembled too (paused at the approval gate,
  // S.review null) — the run most worth discarding.
  function deleteReview() {
    var id = (S.review && S.review.id) || S.openId;
    if (!id) return;
    var title = (S.review && S.review.title) ||
      (S.run && S.run.inputs && (S.run.inputs.title || S.run.inputs.url)) || id;
    EOS_UI.confirmDelete({
      label: title,
      extra: "Its screenshots go with it; a copy already exported to the vault is kept.",
    }).then(async function (yes) {
      if (!yes) return;
      var d = await EOS.apiSafe("/markitup/api/reviews/" + encodeURIComponent(id), { method: "DELETE" });
      if (!d || d.error) { EOS.toast((d && d.error) || "Delete failed", "error"); return; }
      EOS.toast("Review deleted", "success");
      route.clear();
    });
  }

  // ── keyboard triage (approved 2026-10-03): j/k move, a/d/e act ─────
  // Order = the rail's shot order, then the gutter's, over the comments the
  // filters show — moving past the last one on a view opens the next view.
  function triageOrder() {
    var shots = (S.review && S.review.shots) || [];
    var out = [];
    shots.forEach(function (s) {
      (S.review.comments || []).forEach(function (c) {
        if (c.shot_id === s.id && visible(c)) out.push(c);
      });
    });
    return out;
  }

  function selectComment(c) {
    if (!c) return;
    S.selected = c.n;
    if (c.shot_id !== S.shot) { S.shot = c.shot_id; renderRail(); }
    renderCanvas(); renderGutter();
    var note = document.querySelector('.mk-note[data-note="' + c.n + '"]');
    if (note) note.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // Not while a dialog is up: EOS_UI.modal removes its overlay on close, so its
  // presence means the edit form (or a confirm) owns the keyboard.
  // Off on the list (no review), and whenever another surface sits on top of
  // the page: a dialog (EOS_UI.modal's overlay exists only while open), the
  // note reader the export link opens, or the `?` help — a key there must not
  // act on a comment the user cannot see.
  function triageActive() {
    return !!S.review && !$("eos-modal-overlay") &&
      !document.querySelector("#eos-note-overlay.open, #eos-help-overlay.show");
  }

  function step(dir) {
    if (!triageActive()) return;
    var order = triageOrder();
    if (!order.length) return;
    // Place the selection in rail order over ALL comments, so a selection the
    // filters just hid (accept with "Hide resolved" on) still steps to its
    // neighbour instead of jumping back to the first comment.
    var all = (S.review.shots || []).reduce(function (acc, s) {
      return acc.concat((S.review.comments || []).filter(function (c) { return c.shot_id === s.id; }));
    }, []);
    var pos = -1;
    all.forEach(function (c, k) { if (c.n === S.selected) pos = k; });
    var rank = function (c) { return all.indexOf(c); };
    var next;
    if (pos === -1) next = dir > 0 ? order[0] : order[order.length - 1];
    else if (dir > 0) next = order.filter(function (c) { return rank(c) > pos; })[0] || order[order.length - 1];
    else next = order.filter(function (c) { return rank(c) < pos; }).pop() || order[0];
    selectComment(next);
  }

  function actOnSelected(kind) {
    if (!triageActive() || S.selected == null) return;
    var n = S.selected;
    if (kind === "edit") { editComment(n); return; }
    var want = kind === "accept" ? "accepted" : "dismissed";
    var cur = (S.review.comments || []).filter(function (c) { return c.n === n; })[0];
    patchComment(n, { status: cur && cur.status === want ? "open" : want });
  }

  // eos.js injects eos-keys.js as a dynamic script, which usually lands after
  // this file has run — registering once, immediately, finds no EOS.keys.
  (function registerTriageKeys(tries) {
    if (!(window.EOS && EOS.keys && EOS.keys.register)) {
      if (tries > 0) setTimeout(function () { registerTriageKeys(tries - 1); }, 50);
      return;
    }
    EOS.keys.register("j", "Next comment", function () { step(1); });
    EOS.keys.register("k", "Previous comment", function () { step(-1); });
    EOS.keys.register("a", "Accept the selected comment", function () { actOnSelected("accept"); });
    EOS.keys.register("d", "Dismiss the selected comment", function () { actOnSelected("dismiss"); });
    EOS.keys.register("e", "Edit the selected comment", function () { actOnSelected("edit"); });
  })(200);

  // ── boot ──────────────────────────────────────────────────────────

  var settings = EOS_UI.settingsPanel({
    id: "mk-settings-panel", title: "Mark It Up settings", app: "markitup"
  });
  $("mk-settings").addEventListener("click", function () { settings.open(); });

  EOS_UI.modelPill({ app: "markitup", mount: "#model-pill", domain: "text" });

  (async function boot() {
    var d = await EOS.apiSafe("/markitup/api/rubrics");
    ((d && d.rubrics) || []).forEach(function (r) { S.rubrics[r.id] = r; });
    await loadList();
    route.init();
  })();
})();
